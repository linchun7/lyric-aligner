from __future__ import annotations

import unittest

from pr70_doc_export_helper import encoded_documents


class PR70DocumentationExportTests(unittest.TestCase):
    def test_export_updated_owning_documents(self) -> None:
        rows = encoded_documents()
        self.assertEqual(len(rows), 4)
        for path, payload in rows:
            print(f"PR70_DOC_BEGIN::{path}")
            print(payload)
            print(f"PR70_DOC_END::{path}")


if __name__ == "__main__":
    unittest.main()
