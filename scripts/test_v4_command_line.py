from __future__ import annotations

import unittest

from lyric_aligner.command_line import CommandLineParseError, split_external_command


class ExternalCommandLineTests(unittest.TestCase):
    def test_posix_plain_command(self) -> None:
        self.assertEqual(
            split_external_command("aligner --foo bar", windows=False),
            ["aligner", "--foo", "bar"],
        )

    def test_windows_quoted_executable_with_spaces(self) -> None:
        command = '"C:\\Program Files\\Aligner\\aligner.exe" --foo bar'
        self.assertEqual(
            split_external_command(command, windows=True),
            ["C:\\Program Files\\Aligner\\aligner.exe", "--foo", "bar"],
        )

    def test_windows_quoted_argument_with_spaces(self) -> None:
        command = 'aligner --model "model with spaces"'
        self.assertEqual(
            split_external_command(command, windows=True),
            ["aligner", "--model", "model with spaces"],
        )

    def test_windows_single_quotes_are_not_normalized_as_shell_quotes(self) -> None:
        command = "'C:\\Program Files\\Aligner\\aligner.exe' --foo bar"
        self.assertEqual(
            split_external_command(command, windows=True),
            ["'C:\\Program Files\\Aligner\\aligner.exe'", "--foo", "bar"],
        )

    def test_posix_quoted_argument_with_spaces(self) -> None:
        command = 'aligner --model "model with spaces"'
        self.assertEqual(
            split_external_command(command, windows=False),
            ["aligner", "--model", "model with spaces"],
        )

    def test_malformed_quote_fails_closed(self) -> None:
        with self.assertRaises(CommandLineParseError):
            split_external_command('"unterminated', windows=True)
        with self.assertRaises(CommandLineParseError):
            split_external_command('"unterminated', windows=False)


if __name__ == "__main__":
    unittest.main()
