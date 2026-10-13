"""Regression checks for RTL, emoji, overflow and legacy fields."""
import base64
import io
import json
import unittest
from PIL import Image
import render as r
import webhook_server as ws

class RedesignTests(unittest.TestCase):
    def test_hebrew_and_numbers(self):
        self.assertEqual(r.visual('דואר לאיסוף'), 'ףוסיאל ראוד')
        self.assertEqual(r.visual('עודכן 13.10'), '13.10 ןכדוע')
        self.assertEqual(r.visual('12:00-19:00'), '12:00-19:00')
        self.assertEqual(r.visual('איסוף X3'), 'X3 ףוסיא')

    def test_emoji_neutral_for_bidi(self):
        self.assertTrue(r.rtl(r.logical('📦 איסוף X3')))
        self.assertEqual(r.visual('📦 איסוף X3'), 'X3 ףוסיא '+r.ICONS['box'])
        self.assertIn('[U+1F600]', r.visual('Hello 😀'))

    def test_all_fields_and_sizes(self):
        im=Image.new('RGB',(80,60),'black');buf=io.BytesIO();im.save(buf,'PNG')
        p={'title':'דואר X3','text':'עברית English 123', 'items':[['שעות','12:00-19:00']],
           'chart':[1,3,2,5],'chart_title':'מדידות היום',
           'image':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode(),
           'footer':'עודכן 13.10', 'extra':'kept'}
        before=json.dumps(p)
        for w,h in ((792,528),(528,792),(64,64)):
            a=r.render_payload(p,w,h)
            self.assertEqual(a.mode,'1');self.assertEqual(a.size,(w,h))
        self.assertEqual(json.dumps(p),before)

    def test_server_and_cli_match(self):
        p={'title':'שלום','items':[['אחד','123']]}
        self.assertEqual(r.render_payload(p,792,528).tobytes(),ws.render_payload(p,792,528).tobytes())

if __name__=='__main__':unittest.main()
