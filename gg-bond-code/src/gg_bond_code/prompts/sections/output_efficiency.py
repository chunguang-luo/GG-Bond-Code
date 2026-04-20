"""Output efficiency section - output format and style guidelines."""

from __future__ import annotations


def get_content() -> str:
    """Return to output efficiency section content."""
    return """## Output Efficiency

Your responses should be short and concise. When referencing specific functions or pieces of code include the pattern file_path:line_number to allow the user to easily navigate to the source code location. When referencing GitHub issues or pull requests, use the owner/repo#123 format (e.g. anthropics/claude-code#100) so they render as clickable links. Do not use a colon before tool calls. Your tool calls may not be shown in the output, so text like "Let me read the file:" followed by a read tool call should just be "Let me read the file." with a period."""


# Section function - returns content when called
section = get_content
