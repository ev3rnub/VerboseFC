"""Tests for the search-bar line-number gutter (search_page.render_search_tab).

The gutter is a server-rendered markdown element that CSS floats over the
query textarea's left edge when the bar is expanded. These tests check the
rendering logic headlessly via Streamlit's AppTest.

Run: python src/test_gutter.py
"""
import os
import sys
import tempfile

from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_APP = """\
from search_page import render_search_tab

class FakeDB:
    def get_file_count(self):
        return 0

    def get_content_count(self):
        return 0

class FakeStore:
    def unseen_runs(self, seen):
        return 0

render_search_tab(FakeDB(), None, FakeStore())
"""


def _gutters(at):
    return [m.value for m in at.markdown
            if '<div class="vfc-gutter">' in (m.value or '')]


def main():
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as f:
        f.write(_APP)
        app_path = f.name
    try:
        at = AppTest.from_file(app_path, default_timeout=30)
        at.run()
        assert not at.exception, at.exception

        # collapsed (default height): no gutter
        assert at.session_state['q_area_h'] == 44
        assert _gutters(at) == [], 'gutter should be hidden when collapsed'

        # expanded, empty query: gutter shows "1"
        at.session_state['q_area_h'] = 104
        at.run()
        assert not at.exception, at.exception
        assert _gutters(at) == ['<div class="vfc-gutter">1</div>']

        # expanded, 3-line query: numbers 1..3
        at.session_state['vpl_query'] = 'search foo\n| where ext==pdf\n| head 10'
        at.run()
        assert _gutters(at) == ['<div class="vfc-gutter">1<br>2<br>3</div>']

        # the gutter never touches the query text itself
        assert at.session_state['vpl_query'] == \
            'search foo\n| where ext==pdf\n| head 10'

        # collapse again: gutter disappears
        at.session_state['q_area_h'] = 44
        at.run()
        assert _gutters(at) == [], 'gutter should disappear when collapsed'

        print('gutter: all tests passed')
    finally:
        os.unlink(app_path)


if __name__ == '__main__':
    main()
