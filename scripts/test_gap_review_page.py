import shutil
import subprocess
import unittest
import json
import re
from pathlib import Path
from scripts.v4_build_gap_ab_review import render_page


class GapPagePlaybackTests(unittest.TestCase):
    def test_user_text_cannot_be_replaced_as_template_or_close_script(self):
        text='__SCRIPT__ __PAYLOAD__ </script><script>alert(1)</script>'
        template='<script id="payload" type="application/json">__PAYLOAD__</script><script>__SCRIPT__</script>'
        page=render_page(template,{'note':text},'const ok = true;')
        payload=re.search(r'type="application/json">(.*?)</script>',page,re.S).group(1)
        self.assertEqual(json.loads(payload)['note'],text)
        self.assertEqual(page.count('const ok = true;'),1)
        self.assertNotIn('<script>alert(1)</script>',page)

    @unittest.skipUnless(shutil.which('node'), 'Node is unavailable for browser-controller unit checks')
    def test_actual_playback_controller(self):
        result=subprocess.run(['node',str(Path(__file__).with_name('test_gap_review_ui.js'))],
                              capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('checks passed',result.stdout)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable for DOM event checks')
    def test_boundary_editor_dom_events(self):
        result=subprocess.run(['node',str(Path(__file__).with_name('test_gap_review_dom.js'))],
                              capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('invalid export passed',result.stdout)


if __name__=='__main__':unittest.main()
