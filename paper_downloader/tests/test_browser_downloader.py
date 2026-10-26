import unittest
from unittest.mock import MagicMock, patch

from browser_downloader import BrowserDownloader


class TestBrowserDownloader(unittest.TestCase):

    def test_browser_downloader_uninstalled_handling(self):
        downloader = BrowserDownloader()
        with patch.dict("sys.modules", {"playwright.sync_api": None}):
            # Simulate ImportError when importing playwright
            with patch("builtins.__import__", side_effect=ImportError("No module named 'playwright'")):
                ready, reason = downloader.is_available()
                self.assertFalse(ready)
                self.assertIn("Playwright is not installed", reason)

                data, err = downloader.fetch("https://example.com")
                self.assertIsNone(data)
                self.assertIn("Playwright is not installed", err)

    def test_browser_downloader_mocked_fetch(self):
        downloader = BrowserDownloader()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page

        mock_download = MagicMock()
        # Mock download.path() returning a temp file with valid binary
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"%PDF-1.5 test pdf content binary")
            tmp_path = f.name

        mock_download.path.return_value = tmp_path

        # Context manager for expect_download
        class MockExpectDownload:
            def __enter__(self):
                cm = MagicMock()
                cm.value = mock_download
                return cm
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        mock_page.expect_download.return_value = MockExpectDownload()

        downloader._context = mock_context
        downloader._tried_launch = True

        data, err = downloader.fetch("https://example.com/file.pdf", referer="https://example.com")
        self.assertIsNone(err)
        self.assertIsNotNone(data)
        self.assertTrue(data.startswith(b"%PDF-"))

        downloader.close()
        self.assertIsNone(downloader._context)


if __name__ == "__main__":
    unittest.main()
