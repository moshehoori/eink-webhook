"""Shipments screen fits all items, preserves RTL and fails on overflow."""
import copy
import unittest
import render as r


class ShipmentsTests(unittest.TestCase):
    def test_file_data_both_sizes(self):
        payload = {"mode": "shipments"}
        before = copy.deepcopy(payload)
        data = r.shipments_data(payload)
        self.assertEqual(len(data["items"]), 7)
        for w, h in r.SIZES.values():
            image = r.render_payload(payload, w, h)
            self.assertEqual(image.mode, "1")
            self.assertEqual(image.size, (w, h))
        self.assertEqual(payload, before)

    def test_inline_update(self):
        p = {"mode": "shipments", "shipments": {"title": "חבילות", "items": [
            {"name": "חבילה X3", "status": "נאספה"}], "footer": "עודכן 13.10"}}
        self.assertEqual(r.render_payload(p, 792, 528).size, (792, 528))

    def test_overflow_rejected(self):
        data = {"items": [{"name": "חבילה", "status": "x" * 500}]}
        with self.assertRaises(ValueError):
            r.draw_shipments(data, 792, 528)
        data["items"] *= 9
        with self.assertRaises(ValueError):
            r.shipments_data({"shipments": data})


if __name__ == "__main__":
    unittest.main()
