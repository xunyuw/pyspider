#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Author: Binux<i@binux.me>
#         http://binux.me
# Created on 2014-02-22 14:00:05

import os
import time
import unittest2 as unittest
import logging.config
logging.config.fileConfig("logging.conf")

from pyspider.processor.processor import build_module


class TestProjectModule(unittest.TestCase):
    base_task = {
        'taskid': 'taskid',
        'project': 'test.project',
        'url': 'www.baidu.com/',
        'schedule': {
            'priority': 1,
            'retries': 3,
            'exetime': 0,
            'age': 3600,
            'itag': 'itag',
            'recrawl': 5,
        },
        'fetch': {
            'method': 'GET',
            'headers': {
                'Cookie': 'a=b',
            },
            'data': 'a=b&c=d',
            'timeout': 60,
            'save': [1, 2, 3],
        },
        'process': {
            'callback': 'callback',
        },
    }
    fetch_result = {
        'status_code': 200,
        'orig_url': 'www.baidu.com/',
        'url': 'http://www.baidu.com/',
        'headers': {
            'cookie': 'abc',
        },
        'content': 'test data',
        'cookies': {
            'a': 'b',
        },
        'save': [1, 2, 3],
    }

    def setUp(self):
        self.project = "test.project"
        self.script = open(os.path.join(os.path.dirname(__file__), 'data_handler.py')).read()
        self.env = {
            'test': True,
        }
        self.project_info = {
            'name': self.project,
            'status': 'DEBUG',
        }
        data = build_module({
            'name': self.project,
            'script': self.script
        }, {'test': True})
        self.module = data['module']
        self.instance = data['instance']

    def test_2_hello(self):
        self.base_task['process']['callback'] = 'hello'
        ret = self.instance.run(self.module, self.base_task, self.fetch_result)
        self.assertIsNone(ret.exception)
        self.assertEqual(ret.result, "hello world!")

    def test_3_echo(self):
        self.base_task['process']['callback'] = 'echo'
        ret = self.instance.run(self.module, self.base_task, self.fetch_result)
        self.assertIsNone(ret.exception)
        self.assertEqual(ret.result, "test data")

    def test_4_saved(self):
        self.base_task['process']['callback'] = 'saved'
        ret = self.instance.run(self.module, self.base_task, self.fetch_result)
        self.assertIsNone(ret.exception)
        self.assertEqual(ret.result, self.base_task['fetch']['save'])

    def test_5_echo_task(self):
        self.base_task['process']['callback'] = 'echo_task'
        ret = self.instance.run(self.module, self.base_task, self.fetch_result)
        self.assertIsNone(ret.exception)
        self.assertEqual(ret.result, self.project)

    def test_6_catch_status_code(self):
        self.fetch_result['status_code'] = 403
        self.base_task['process']['callback'] = 'catch_status_code'
        ret = self.instance.run(self.module, self.base_task, self.fetch_result)
        self.assertIsNone(ret.exception)
        self.assertEqual(ret.result, 403)
        self.fetch_result['status_code'] = 200

    def test_7_raise_exception(self):
        self.base_task['process']['callback'] = 'raise_exception'
        ret = self.instance.run(self.module, self.base_task, self.fetch_result)
        self.assertIsNotNone(ret.exception)
        logstr = ret.logstr()
        self.assertIn('info', logstr)
        self.assertIn('warning', logstr)
        self.assertIn('error', logstr)

    def test_8_add_task(self):
        self.base_task['process']['callback'] = 'add_task'
        ret = self.instance.run(self.module, self.base_task, self.fetch_result)
        self.assertIsNone(ret.exception)
        self.assertEqual(len(ret.follows), 1)
        self.assertEqual(len(ret.messages), 1)

    def test_10_cronjob(self):
        task = {
            'taskid': '_on_cronjob',
            'project': self.project,
            'url': 'data:,_on_cronjob',
            'fetch': {
                'save': {
                    'tick': 11,
                },
            },
            'process': {
                'callback': '_on_cronjob',
            },
        }
        fetch_result = dict(self.fetch_result)
        fetch_result['save'] = {
            'tick': 11,
        }
        ret = self.instance.run(self.module, task, fetch_result)
        logstr = ret.logstr()
        self.assertNotIn('on_cronjob1', logstr)
        self.assertNotIn('on_cronjob2', logstr)

        task['fetch']['save']['tick'] = 10
        fetch_result['save'] = task['fetch']['save']
        ret = self.instance.run(self.module, task, fetch_result)
        logstr = ret.logstr()
        self.assertNotIn('on_cronjob1', logstr)
        self.assertIn('on_cronjob2', logstr)

        task['fetch']['save']['tick'] = 60
        fetch_result['save'] = task['fetch']['save']
        ret = self.instance.run(self.module, task, fetch_result)
        logstr = ret.logstr()
        self.assertIn('on_cronjob1', logstr)
        self.assertIn('on_cronjob2', logstr)

    def test_20_get_info(self):
        task = {
            'taskid': '_on_get_info',
            'project': self.project,
            'url': 'data:,_on_get_info',
            'fetch': {
                'save': ['min_tick', ],
            },
            'process': {
                'callback': '_on_get_info',
            },
        }
        fetch_result = dict(self.fetch_result)
        fetch_result['save'] = task['fetch']['save']

        ret = self.instance.run(self.module, task, fetch_result)
        self.assertEqual(len(ret.follows), 1, ret.logstr())
        for each in ret.follows:
            self.assertEqual(each['url'], 'data:,on_get_info')
            self.assertEqual(each['fetch']['save']['min_tick'], 10)

import shutil
import inspect
from multiprocessing import Queue
from pyspider.database.sqlite import projectdb
from pyspider.processor.processor import Processor
from pyspider.libs.utils import run_in_thread
from pyspider.libs import sample_handler


class TestProcessor(unittest.TestCase):
    projectdb_path = './data/tests/project.db'

    @classmethod
    def setUpClass(self):
        shutil.rmtree('./data/tests/', ignore_errors=True)
        os.makedirs('./data/tests/')

        def get_projectdb():
            return projectdb.ProjectDB(self.projectdb_path)
        self.projectdb = get_projectdb()
        self.in_queue = Queue(10)
        self.status_queue = Queue(10)
        self.newtask_queue = Queue(10)
        self.result_queue = Queue(10)

        def run_processor():
            self.processor = Processor(get_projectdb(), self.in_queue,
                                       self.status_queue, self.newtask_queue, self.result_queue)
            self.processor.CHECK_PROJECTS_INTERVAL = 0.1
            self.processor.run()
        self.process = run_in_thread(run_processor)
        time.sleep(1)

    @classmethod
    def tearDownClass(self):
        if self.process.is_alive():
            self.processor.quit()
            self.process.join(2)
        assert not self.process.is_alive()
        shutil.rmtree('./data/tests/', ignore_errors=True)

    def test_10_update_project(self):
        self.assertEqual(len(self.processor.projects), 0)
        self.projectdb.insert('test_project', {
            'name': 'test_project',
            'group': 'group',
            'status': 'TODO',
            'script': inspect.getsource(sample_handler),
            'comments': 'test project',
            'rate': 1.0,
            'burst': 10,
        })

        task = {
            "process": {
                "callback": "on_start"
            },
            "project": "not_exists",
            "taskid": "data:,on_start",
            "url": "data:,on_start"
        }
        self.in_queue.put((task, {}))
        time.sleep(1)
        self.assertTrue(self.status_queue.empty())
        self.assertEqual(len(self.processor.projects), 1)

    def test_30_new_task(self):
        self.assertTrue(self.status_queue.empty())
        self.assertTrue(self.newtask_queue.empty())
        task = {
            "process": {
                "callback": "on_start"
            },
            "project": "test_project",
            "taskid": "data:,on_start",
            "url": "data:,on_start"
        }
        fetch_result = {
            "orig_url": "data:,on_start",
            "content": "on_start",
            "headers": {},
            "status_code": 200,
            "url": "data:,on_start",
            "time": 0,
        }
        self.in_queue.put((task, fetch_result))
        time.sleep(1)
        self.assertFalse(self.status_queue.empty())
        while not self.status_queue.empty():
            self.status_queue.get()
        self.assertFalse(self.newtask_queue.empty())

    def test_40_index_page(self):
        task = None
        while not self.newtask_queue.empty():
            task = self.newtask_queue.get()
        self.assertIsNotNone(task)

        fetch_result = {
            "orig_url": task['url'],
            "content": (
                "<html><body>"
                "<a href='http://binux.me'>binux</a>"
                "<a href='http://binux.me/中文'>binux</a>"
                "</body></html>"
            ),
            "headers": {},
            "status_code": 200,
            "url": task['url'],
            "time": 0,
        }
        self.in_queue.put((task, fetch_result))
        time.sleep(1)
        self.assertFalse(self.status_queue.empty())
        self.assertFalse(self.newtask_queue.empty())
        task = self.newtask_queue.get()
        self.assertEqual(task['url'], 'http://binux.me/')
        task = self.newtask_queue.get()
        self.assertTrue(task['url'].startswith('http://binux.me/%'), task['url'])
