"""Offline regression checks at real selection, model-validation and publication seams."""
import datetime as dt
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error
import uuid

import engine as d


NOW = dt.datetime(2026, 10, 22, 12, tzinfo=d.TZ)
WORK = Path(__file__).resolve().parents[1] / 'test-state' / ('engine-' + uuid.uuid4().hex[:8])
WORK.mkdir(parents=True)


def row(title='A sufficiently long original news headline', url='https://example.com/story', **kw):
    return dict(title=title, link=url, publishedAt=NOW.timestamp()*1000, source='Publisher A', **kw)


def feed(items):
    return dict(categories=dict(ai=dict(items=items)))


def cfg():
    return dict(model='deepseek-flash', variants=['full', 'tech', 'finance'], max_articles=12,
                per_publisher=2, minimum_articles=1)


def claim(quote='A sufficiently long original news headline'):
    return dict(events=[dict(topic='科技与AI', headline='事件标签', claims=[dict(
        text='有据转述', evidence=[dict(source_id='S1', quote=quote)])])])


class Regression(unittest.TestCase):
    def test_dates_missing_expired_future_nan_distinct(self):
        missing=row(); missing.pop('publishedAt')
        old=row(); old['publishedAt'] -= 90000*1000
        future=row(); future['publishedAt'] += 3600*1000
        nan=row(); nan['publishedAt'] = 'NaN'
        items, q=d.prepare({'full':feed([row(),missing,old,future,nan])},cfg(),NOW)
        self.assertEqual(len(items),1)
        self.assertEqual([q[k] for k in ('missing_date','old','future','invalid_date')],[1,1,1,1])

    def test_url_tracking_merge_preserves_topics(self):
        payloads={'full':feed([row(url='https://example.com/a?utm_source=x&id=2#fragment')]),
                  'finance':feed([row(url='https://example.com/a?id=2')])}
        items,q=d.prepare(payloads,cfg(),NOW)
        self.assertEqual(len(items),1)
        self.assertIn('全球大事',items[0]['topics']); self.assertIn('经济',items[0]['topics'])
        self.assertEqual(q['duplicate_url'],1)
        self.assertIsNone(d.canonical_url('javascript:alert(1)'))
        self.assertIsNone(d.canonical_url('https://user:password@example.com/'))
        self.assertNotEqual(d.canonical_url('https://e.com/?id=1'),d.canonical_url('https://e.com/?id=2'))

    def test_conservative_title_dedup(self):
        data=[row('Company raises $10 million'),row('Company raises $10 million',url='https://e.com/b'),
              row('Company does not raise $10 million',url='https://e.com/c'),
              row('Company raises $20 million',url='https://e.com/d')]
        c=cfg(); c['per_publisher']=10
        items,q=d.prepare({'full':feed(data)},c,NOW)
        self.assertEqual(len(items),3)
        self.assertEqual(q['duplicate_title'],1)

    def test_publisher_cap_supply_allocation(self):
        data=[dict(row(f'Semiconductor supply chain news number {i}',url=f'https://e.com/{i}'),source='Noisy publisher') for i in range(8)]
        data.append(dict(row('Shipping freight tariffs rise in important port',url='https://b.com/'),source='Other publisher'))
        items,q=d.prepare({'full':feed(data)},cfg(),NOW)
        self.assertEqual(len(items),3)
        self.assertEqual(q['topic_counts']['供应链'],3)
        self.assertEqual(q['publisher_cap'],6)

    def test_citations_reject_wrong_source_and_fabricated_quote(self):
        items,_=d.prepare({'full':feed([row()])},cfg(),NOW)
        self.assertEqual(d.verify_claims(claim(),items),claim())
        with self.assertRaises(ValueError): d.verify_claims(claim('A fabricated claim unsupported by input'),items)
        bad=claim(); bad['events'][0]['claims'][0]['evidence'][0]['source_id']='S999'
        with self.assertRaises(ValueError): d.verify_claims(bad,items)

    def test_model_post_is_not_retried(self):
        error=urllib.error.HTTPError('https://api.deepseek.com/',503,'unavailable',{},None)
        opener=unittest.mock.Mock(); opener.open.side_effect=error
        with patch.object(d.urllib.request,'build_opener',return_value=opener):
            with self.assertRaises(RuntimeError): d.request_json('https://api.deepseek.com/',data={})
        self.assertEqual(opener.open.call_count,1)

    def test_atomic_publish_interruption_and_no_overwrite(self):
        target=WORK/'vault'/'test.md'; target.parent.mkdir(exist_ok=True)
        items,q=d.prepare({'full':feed([row()])},cfg(),NOW)
        content=d.render(NOW,cfg(),items,claim(),q,{},dict(available=False,reason='无昨日数据'),{},'test')
        with patch.object(d.os,'link',side_effect=OSError('injected interruption')):
            with self.assertRaises(OSError): d.publish_note(target,content,'interrupted')
        self.assertFalse(target.exists())
        d.publish_note(target,content,'complete')
        self.assertTrue(d.valid_note(target))
        before=target.read_bytes()
        with self.assertRaises(FileExistsError): d.publish_note(target,content,'duplicate')
        self.assertEqual(before,target.read_bytes())
        broken=WORK/'broken.md'; broken.write_text('---\n# 全球信息日报\npartial',encoding='utf-8')
        self.assertFalse(d.valid_note(broken))

    def test_end_to_end_failure_recovery_and_idempotence(self):
        root=WORK/'pipeline'; root.mkdir()
        vault=root/'vault'; (vault/'.obsidian').mkdir(parents=True)
        c=dict(cfg(),vault=str(vault),folder='日报')
        current=dt.datetime.now(d.TZ)
        record=row(); record['publishedAt']=current.timestamp()*1000
        with patch.object(d,'fetch_payloads',return_value=({}, {'full':'proxy timeout'})), patch.object(d,'summarize') as model:
            with self.assertRaises(RuntimeError): d.execute(c,root,scheduled=True)
            model.assert_not_called()
        status=json.loads((root/'latest-run.json').read_text(encoding='utf-8'))
        self.assertEqual(status['result'],'failed'); self.assertEqual(status['stage'],'selection')
        self.assertIn('最近运行失败',(vault/'日报'/'00-阅读入口.md').read_text(encoding='utf-8'))
        with patch.object(d,'fetch_payloads',return_value=({'full':feed([record])},{})), patch.object(d,'summarize',return_value=(claim(),{})) as model:
            result=d.execute(c,root,scheduled=True)
            self.assertEqual(result['result'],'published')
            note=Path(result['note']); before=note.read_bytes()
            again=d.execute(c,root,scheduled=True)
            self.assertEqual(again['result'],'skipped-valid'); self.assertEqual(model.call_count,1)
            self.assertEqual(before,note.read_bytes())
        obs=json.loads((root/'observation.json').read_text(encoding='utf-8'))
        self.assertEqual(obs['observed_days'],1)
        day=obs['days'][current.date().isoformat()]
        self.assertEqual(day['failures'],1); self.assertTrue(day['available'])

    def test_lock_blocks_overlapping_runs(self):
        with d.run_lock(WORK):
            with self.assertRaises(RuntimeError):
                with d.run_lock(WORK): pass

    def test_model_failure_publishes_no_note_and_recovers(self):
        root=WORK/'model-failure'; root.mkdir()
        vault=root/'vault'; (vault/'.obsidian').mkdir(parents=True)
        c=dict(cfg(),vault=str(vault),folder='日报')
        record=row(); record['publishedAt']=dt.datetime.now(d.TZ).timestamp()*1000
        with patch.object(d,'fetch_payloads',return_value=({'full':feed([record])},{})):
            with patch.object(d,'summarize',side_effect=RuntimeError('injected model timeout')):
                with self.assertRaises(RuntimeError): d.execute(c,root)
            self.assertFalse(list((vault/'日报').glob('????-??-??.md')))
            self.assertEqual(json.loads((root/'latest-run.json').read_text(encoding='utf-8'))['stage'],'summarization')
            with patch.object(d,'summarize',return_value=(claim(),{})):
                self.assertEqual(d.execute(c,root)['result'],'published')

    def test_legacy_note_and_missing_yesterday_are_explicit(self):
        legacy=WORK/'legacy.md'
        legacy.write_text('---\nmodel: old\n---\n# 全球信息日报\n[S1](https://e.com)\n## 原始来源\n## 采集状态\n```json\n{}\n```',encoding='utf-8')
        self.assertTrue(d.valid_note(legacy))
        self.assertFalse(d.compare_yesterday(WORK,NOW,[])['available'])
