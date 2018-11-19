import argparse
import unittest
import sys
import os

from tests.cleantest import CleanTestLoader


parser = argparse.ArgumentParser(description='Run patch tests.')
parser.add_argument('dir', metavar='directory', type=str,
                    help='directory to search for patch tests')


class IntegrationTestLoader(unittest.TestLoader):
    def _match_path(self, path, full_path, pattern):
        return 'test_patch' not in path and 'test_patch' not in full_path


def main():
    args = parser.parse_args()
    cwd = os.getcwd()
    sys.path.pop(0)
    sys.path.insert(0, cwd)
    test_dir = os.path.join(cwd, args.dir)
    modprefix = args.dir.replace(os.path.sep, '.')

    loader = IntegrationTestLoader()
    patch_loader = CleanTestLoader(modprefix)

    suite = unittest.TestSuite([
        loader.discover(test_dir, top_level_dir=cwd),
        patch_loader.discover(test_dir, pattern='test_patch.py', top_level_dir=cwd),
    ])
    result = unittest.TextTestRunner().run(suite)
    sys.exit(not result.wasSuccessful())


if __name__ == '__main__':
    main()
