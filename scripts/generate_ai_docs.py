import argparse
import os
import re


def read_guidelines(filepath="AI_GUIDELINES.md"):
    """Reads the AI_GUIDELINES.md file and returns its content."""
    with open(filepath, "r") as f:
        return f.read()


def write_cursor_file(content):
    """Writes the content to the .cursor-mdc file."""
    if not os.path.exists(".cursor"):
        os.makedirs(".cursor")

    with open(".cursor/dd-trace-py.mdc", "w") as f:
        f.write(content)


def write_claude_file(content):
    """Writes the content to the CLAUDE.md file."""
    with open("CLAUDE.md", "w") as f:
        f.write(content)


def main():
    """Main function to generate AI docs."""
    parser = argparse.ArgumentParser(description="Generate AI docs from AI_GUIDELINES.md")
    parser.add_argument(
        "--guidelines-file",
        type=str,
        default="AI_GUIDELINES.md",
        help="Path to the AI_GUIDELINES.md file.",
    )
    args = parser.parse_args()

    content = read_guidelines(args.guidelines_file)

    write_cursor_file(content)
    write_claude_file(content)

    print("Successfully generated .cursor/dd-trace-py.mdc and CLAUDE.md")


if __name__ == "__main__":
    main() 