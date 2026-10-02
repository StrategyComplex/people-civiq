"""Strict source decoding and POSIX no-follow, per-file output replacement."""

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import uuid

import yaml


class StrictSafeLoader(yaml.SafeLoader):
    """Reject duplicate keys, merge directives and recursive alias graphs."""

    def construct_document(self, node):
        active, visited = set(), set()

        def visit(current):
            identity = id(current)
            if identity in active:
                raise yaml.constructor.ConstructorError(
                    None, None, 'recursive YAML aliases are not supported', current.start_mark)
            if identity in visited:
                return
            active.add(identity)
            if isinstance(current, yaml.MappingNode):
                children = [child for pair in current.value for child in pair]
            elif isinstance(current, yaml.SequenceNode):
                children = current.value
            else:
                children = []
            for child in children:
                visit(child)
            active.remove(identity)
            visited.add(identity)

        visit(node)
        return super().construct_document(node)

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == 'tag:yaml.org,2002:merge':
                raise yaml.constructor.ConstructorError(
                    None, None, 'YAML merge directives are not supported', key_node.start_mark)
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError as error:
                raise yaml.constructor.ConstructorError(
                    None, None, 'unhashable YAML mapping key', key_node.start_mark) from error
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None, None, 'duplicate YAML mapping key', key_node.start_mark)
        return super().construct_mapping(node, deep=deep)


def load_yaml(path):
    """Decode without caches or source writes; safe tags retain date support."""
    with open(path, encoding='utf-8') as source:
        return yaml.load(source, Loader=StrictSafeLoader)


def check_path(path):
    """Reject links (including dangling links) before resolving any component."""
    path = Path(path).absolute()
    for component in reversed((path, *path.parents)):
        if component.is_symlink():
            raise ValueError('Symlink output path: ' + str(component))
    return path


def check_output_tree(path):
    """Preflight existing descendants, including files normally skipped."""
    path = check_path(path)
    if path.exists():
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                check_path(Path(directory) / name)


@contextmanager
def output_file(path, *, newline=None):
    """Write a fresh inode then replace via a pinned, no-follow parent descriptor.

    Never truncate an existing inode (which might be hard-linked to source).
    Whole-dataset validation/publication remains the caller's responsibility.
    """
    path = check_path(path)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = '.converter-' + uuid.uuid4().hex
    created = False
    try:
        for part in path.parts[1:-1]:
            try:
                os.mkdir(part, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        try:
            existing = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise ValueError('Output must be a regular file: ' + str(path))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o666, dir_fd=descriptor)
        created = True
        with os.fdopen(fd, 'w', encoding='utf-8', newline=newline) as output:
            yield output
        os.replace(temporary, path.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        created = False
    finally:
        if created:
            os.unlink(temporary, dir_fd=descriptor)
        os.close(descriptor)
