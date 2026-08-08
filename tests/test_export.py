import csv
import json
from pathlib import Path
import tempfile
import unittest

from fd_training_ocr.export import FormRecord, apply_review, run_batch


def record(path, digest, status="succeeded", name='Synthetic, "Member"'):
    fields = {"date":{"raw":"1/2/26","normalized":"2026-01-02"}, "description":{"raw":"Line, one","normalized":"Line, one"}}
    return FormRecord(path.name, digest, 1, "synthetic", "v1", status, fields, {}, ({"row":1,"unit_id":"X1","print_name":name},), (), {"status":"not_required"})


class BatchTests(unittest.TestCase):
    def test_idempotent_directory_partial_failure_and_csv_escaping(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/"in"; output=root/"out"; source.mkdir()
            (source/"good.pdf").write_bytes(b"good"); (source/"bad.pdf").write_bytes(b"bad")
            def processor(path,digest):
                if path.name=="bad.pdf": raise RuntimeError("synthetic failure")
                return record(path,digest)
            first=run_batch(source,output,processor)
            self.assertEqual((first.succeeded,first.failed,first.exit_code),(1,1,2))
            self.assertEqual(len(list((output/"errors").glob("*.json"))),1)
            second=run_batch(source,output,processor)
            self.assertEqual(second.skipped_duplicate,1); self.assertEqual(second.succeeded,0)
            with (output/"attendees.csv").open(encoding="utf-8-sig",newline="") as stream:
                self.assertEqual(list(csv.DictReader(stream))[0]["print_name"],'Synthetic, "Member"')
            self.assertNotIn("signature", (output/"records"/next((output/"records").iterdir()).name).read_text().casefold())

    def test_review_exit_and_signature_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); pdf=root/"one.pdf"; pdf.write_bytes(b"one")
            summary=run_batch(pdf,root/"out",lambda p,h:record(p,h,"review_required"))
            self.assertEqual(summary.exit_code,3)
            with self.assertRaisesRegex(ValueError,"signature"):
                FormRecord("x", "0"*64, 1, "x", "v1", "succeeded", {"attendee.01.signature":{"raw":"x"}}, {})

    def test_single_file_duplicate_hash_even_under_new_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); a=root/"a.pdf"; b=root/"b.pdf"; a.write_bytes(b"same"); b.write_bytes(b"same")
            out=root/"out"; self.assertEqual(run_batch(a,out,lambda p,h:record(p,h)).succeeded,1)
            self.assertEqual(run_batch(b,out,lambda p,h:record(p,h)).skipped_duplicate,1)

    def test_review_preserves_machine_values_and_provenance(self):
        original=record(Path("x.pdf"),"0"*64,"review_required")
        corrected=apply_review(original,{"schema_version":1,"corrections":[{"field_name":"date","reviewed_value":"2026-01-03","status":"corrected","reviewed_at":"2026-08-08T12:00:00Z"},{"field_name":"description","reviewed_value":"Line, one","status":"approved","reviewed_at":"2026-08-08T12:00:00Z"}]})
        self.assertEqual(corrected.fields["date"]["raw"],"1/2/26")
        self.assertEqual(corrected.fields["date"]["reviewed_value"],"2026-01-03")
        self.assertEqual(corrected.review["status"],"completed")
