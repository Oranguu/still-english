import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
import packages


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        cls.patches=[patch.object(packages,'ROOT',cls.root),patch.object(server,'ROOT',cls.root),patch.object(server,'STATE',cls.root/'.state')]
        for p in cls.patches:p.start()
        cls.folder=cls.root/'source'/'sample';cls.folder.mkdir(parents=True)
        (cls.folder/'video.mp4').write_bytes(b'0123456789abcdefghijklmnopqrstuvwxyz')
        cls.manifest={"schema_version":1,"id":"sample","title":"Sample","video":"video.mp4","segments":[{"id":"s1","start":0,"end":2,"en":"Hello.","analysis":{"meaning":"A friendly greeting."}}]}
        packages.atomic_json(cls.folder/'manifest.json',cls.manifest)
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close()
        for p in reversed(cls.patches):p.stop()
        cls.temp.cleanup()

    def request(self,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=10)
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse();result=(response.status,dict(response.getheaders()),response.read());connection.close();return result

    def post(self,path,data):
        return self.request('POST',path,json.dumps(data),{'Content-Type':'application/json','X-Local-Token':server.TOKEN})

    def test_html_and_media_ranges(self):
        self.assertEqual(self.request('GET','/')[0],200)
        self.assertEqual(self.request('GET','/api/packages')[0],200)
        status,headers,data=self.request('GET','/media/sample/video.mp4',headers={'Range':'bytes=3-7'})
        self.assertEqual((status,data),(206,b'34567'));self.assertEqual(headers['Content-Range'],'bytes 3-7/36')
        self.assertEqual(self.request('GET','/media/sample/video.mp4',headers={'Range':'bytes=-3'})[2],b'xyz')
        self.assertEqual(self.request('GET','/media/sample/video.mp4',headers={'Range':'bytes=1000-'})[0],416)
        status,headers,data=self.request('HEAD','/media/sample/video.mp4')
        self.assertEqual(status,200);self.assertEqual(data,b'');self.assertEqual(headers['Content-Length'],'36')

    def test_cross_site_and_traversal_rejected(self):
        self.assertEqual(self.request('GET','/api/packages',headers={'Origin':'https://example.com'})[0],403)
        self.assertEqual(self.request('GET','/api/packages',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('POST','/api/progress','{}')[0],403)
        self.assertEqual(self.request('GET','/media/sample/%2e%2e/server.py')[0],400)

    def test_progress_persists(self):
        data={'folder':'sample','progress':{'lastTime':1.2,'favorites':['s1'],'notes':{'s1':'practice'}}}
        self.assertEqual(self.post('/api/progress',data)[0],200)
        result=json.loads(self.request('GET','/api/progress?folder=sample')[2])
        self.assertEqual(result,data['progress'])

    def test_favorites_join_reviews_and_review_delete_updates_only_favorites(self):
        progress={'favorites':['s1'],'notes':{'s1':'Initial sentence note'},'lastTime':1.6,'dictation':{'s1':'hello'}}
        self.assertEqual(self.post('/api/progress',{'folder':'sample','progress':progress})[0],200)
        rows=json.loads(self.request('GET','/api/reviews')[2])['items']
        favorite=next(row for row in rows if row['kind']=='favorite' and row['source']['package_id']=='sample')
        self.assertEqual((favorite['category'],favorite['english'],favorite['selections']),('sentence','Hello.',[]))
        self.assertEqual(self.post('/api/reviews/update',{'id':favorite['id'],'category':'word'})[0],400)
        self.assertEqual(self.post('/api/reviews/update',{'id':favorite['id'],'note':'My own review note','action':'reviewed'})[0],200)
        self.assertEqual(self.post('/api/reviews/delete',{'id':favorite['id']})[0],200)
        self.assertEqual(json.loads(self.request('GET','/api/progress?folder=sample')[2]),progress|{'favorites':[]})
        self.assertFalse(any(row['id']==favorite['id'] for row in json.loads(self.request('GET','/api/reviews')[2])['items']))
        self.assertEqual(self.post('/api/progress',{'folder':'sample','progress':progress})[0],200)
        restored=next(row for row in json.loads(self.request('GET','/api/reviews')[2])['items'] if row['id']==favorite['id'])
        self.assertEqual((restored['note'],restored['review_count']),('My own review note',1))

    def test_progress_api_rejects_symlink_targets(self):
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            state=Path(temp)
            target=state/'private.json';target.write_text('{"secret":"untouched"}')
            with patch.object(server,'STATE',state):
                path=server.progress_path('sample');path.parent.mkdir()
                path.symlink_to(target)
                self.assertEqual(self.request('GET','/api/progress?folder=sample')[0],400)
                self.assertEqual(self.post('/api/progress',{'folder':'sample','progress':{'favorites':[]}})[0],400)
                self.assertEqual(target.read_text(),'{"secret":"untouched"}')

    def test_streamed_folder_import_and_duplicate(self):
        manifest=self.manifest|{'id':'new-sample'}
        status,_,raw=self.post('/api/import/start',{'manifest':manifest});self.assertEqual(status,200)
        start=json.loads(raw)
        status,_,_=self.request('POST',f'/api/import/file?id={start["id"]}&path=video.mp4',b'video bytes',{'X-Local-Token':server.TOKEN})
        self.assertEqual(status,200)
        status,_,raw=self.post('/api/import/finish',{'id':start['id']});self.assertEqual(status,200)
        folder=json.loads(raw)['folder'];self.assertEqual((self.root/'source'/folder/'video.mp4').read_bytes(),b'video bytes')
        self.assertFalse((self.root/folder).exists())
        status,_,raw=self.post('/api/import/start',{'manifest':manifest});self.assertEqual(json.loads(raw)['existing'],folder)

    def test_import_missing_assets_cannot_finish(self):
        start=json.loads(self.post('/api/import/start',{'manifest':self.manifest|{'id':'missing'}})[2])
        self.assertEqual(self.post('/api/import/finish',{'id':start['id']})[0],400)

    def test_review_roundtrip_and_same_origin_protection(self):
        data={'folder':'sample','segment_id':'s1','selections':[{'field':'analysis.meaning','start':2,'end':10,'quote':'friendly'}]}
        self.assertEqual(self.request('POST','/api/reviews/add',json.dumps(data))[0],403)
        self.assertEqual(self.request('GET','/api/reviews',headers={'Origin':'https://example.com'})[0],403)
        status,_,raw=self.post('/api/reviews/add',data)
        self.assertEqual(status,200)
        item=json.loads(raw)['item'];identifier=item['id']
        self.assertEqual(item['source']['package_id'],'sample')
        self.assertEqual(self.post('/api/reviews/update',{'id':identifier,'note':'My practice note','action':'reviewed','category':'word'})[0],200)
        saved=json.loads(self.request('GET','/api/reviews')[2])['items']
        updated=next(row for row in saved if row['id']==identifier)
        self.assertEqual(updated['note'],'My practice note')
        self.assertEqual(updated['review_count'],1)
        self.assertEqual(updated['category'],'word')
        self.assertEqual(self.post('/api/reviews/delete',{'id':'../escape'})[0],400)
        self.assertEqual(self.post('/api/reviews/delete',{'id':identifier})[0],200)

    def test_analyze_upgrade_scope_passed_without_running_ai(self):
        with patch.object(server.jobs,'launch',return_value={'status':'queued'}) as launch:
            self.assertEqual(self.post('/api/analyze',{'folder':'sample','upgrade':True,'segment_id':'s1'})[0],202)
            launch.assert_called_once_with('analysis',{'folder':'sample','upgrade':True,'segment_id':'s1'})
            self.assertEqual(self.post('/api/analyze',{'folder':'sample','upgrade':'yes'})[0],400)
            self.assertEqual(self.post('/api/analyze',{'folder':'sample','segment_id':'missing'})[0],400)


if __name__=='__main__':unittest.main()
