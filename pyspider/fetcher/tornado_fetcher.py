#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Author: Binux<i@binux.me>
#         http://binux.me
# Created on 2012-12-17 11:07:19

import time
import json
import Queue
import logging
import threading
import cookie_utils
import tornado.ioloop
import tornado.httputil
import tornado.httpclient
from tornado.curl_httpclient import CurlAsyncHTTPClient
from tornado.simple_httpclient import SimpleAsyncHTTPClient
from pyspider.libs import utils, dataurl, counter
logger = logging.getLogger('fetcher')


class MyCurlAsyncHTTPClient(CurlAsyncHTTPClient):

    def free_size(self):
        return len(self._free_list)

    def size(self):
        return len(self._curls) - self.free_size()


class MySimpleAsyncHTTPClient(SimpleAsyncHTTPClient):

    def free_size(self):
        return self.max_clients - self.size()

    def size(self):
        return len(self.active)

fetcher_output = {
    "status_code": int,
    "orig_url": str,
    "url": str,
    "headers": dict,
    "content": str,
    "cookies": dict,
}


class Fetcher(object):
    user_agent = "pyspider/master (+http://pyspider.org/)"
    default_options = {
        'method': 'GET',
        'headers': {},
        'timeout': 120,
    }
    phantomjs_proxy = None

    def __init__(self, inqueue, outqueue, poolsize=100, proxy=None, async=True):
        self.inqueue = inqueue
        self.outqueue = outqueue

        self.poolsize = poolsize
        self._running = False
        self._quit = False
        self.proxy = proxy
        self.async = async

        if async:
            self.http_client = MyCurlAsyncHTTPClient(max_clients=self.poolsize)
        else:
            self.http_client = tornado.httpclient.HTTPClient(
                MyCurlAsyncHTTPClient, max_clients=self.poolsize
            )

        self._cnt = {
            '5m': counter.CounterManager(
                lambda: counter.TimebaseAverageWindowCounter(30, 10)),
            '1h': counter.CounterManager(
                lambda: counter.TimebaseAverageWindowCounter(60, 60)),
        }

    def send_result(self, type, task, result):
        """type in ('data', 'http')"""
        if self.outqueue:
            try:
                self.outqueue.put((task, result))
            except Exception as e:
                logger.exception(e)

    def fetch(self, task, callback=None):
        url = task.get('url', 'data:,')
        if callback is None:
            callback = self.send_result
        if url.startswith('data:'):
            return self.data_fetch(url, task, callback)
        elif task.get('fetch', {}).get('fetch_type') in ('js', 'phantomjs'):
            return self.phantomjs_fetch(url, task, callback)
        else:
            return self.http_fetch(url, task, callback)

    def sync_fetch(self, task):
        wait_result = threading.Condition()
        _result = {}

        def callback(type, task, result):
            wait_result.acquire()
            _result['type'] = type
            _result['task'] = task
            _result['result'] = result
            wait_result.notify()
            wait_result.release()
        self.fetch(task, callback=callback)

        wait_result.acquire()
        while 'result' not in _result:
            wait_result.wait()
        wait_result.release()
        return _result['result']

    def data_fetch(self, url, task, callback):
        self.on_fetch('data', task)
        result = {}
        result['orig_url'] = url
        result['content'] = dataurl.decode(url)
        result['headers'] = {}
        result['status_code'] = 200
        result['url'] = url
        result['cookies'] = {}
        result['time'] = 0
        result['save'] = task.get('fetch', {}).get('save')
        if len(result['content']) < 70:
            logger.info("[200] %s 0s", url)
        else:
            logger.info(
                "[200] data:,%s...[content:%d] 0s",
                result['content'][:70],
                len(result['content'])
            )

        callback('data', task, result)
        self.on_result('data', task, result)
        return task, result

    allowed_options = ['method', 'data', 'timeout', 'allow_redirects', 'cookies']

    def http_fetch(self, url, task, callback):
        self.on_fetch('http', task)
        fetch = dict(self.default_options)
        fetch.setdefault('url', url)
        fetch.setdefault('headers', {})
        fetch.setdefault('allow_redirects', True)
        fetch.setdefault('use_gzip', True)
        fetch['headers'].setdefault('User-Agent', self.user_agent)
        task_fetch = task.get('fetch', {})
        for each in self.allowed_options:
            if each in task_fetch:
                fetch[each] = task_fetch[each]
        fetch['headers'].update(task_fetch.get('headers', {}))

        track_headers = task.get('track', {}).get('fetch', {}).get('headers', {})
        # proxy
        if 'proxy' in task_fetch:
            if isinstance(task_fetch['proxy'], basestring):
                fetch['proxy_host'] = task_fetch['proxy'].split(":")[0]
                fetch['proxy_port'] = int(task_fetch['proxy'].split(":")[1])
            elif self.proxy and task_fetch.get('proxy', True):
                fetch['proxy_host'] = self.proxy.split(":")[0]
                fetch['proxy_port'] = int(self.proxy.split(":")[1])
        # etag
        if task_fetch.get('etag', True):
            _t = task_fetch.get('etag') if isinstance(task_fetch.get('etag'), basestring) \
                else track_headers.get('etag')
            if _t:
                fetch['headers'].setdefault('If-None-Match', _t)
        # last modifed
        if task_fetch.get('last_modified', True):
            _t = task_fetch.get('last_modifed') \
                if isinstance(task_fetch.get('last_modifed'), basestring) \
                else track_headers.get('last-modified')
            if _t:
                fetch['headers'].setdefault('If-Modifed-Since', _t)

        # fix for tornado request obj
        if 'allow_redirects' in fetch:
            fetch['follow_redirects'] = fetch['allow_redirects']
            del fetch['allow_redirects']
        if 'timeout' in fetch:
            fetch['connect_timeout'] = fetch['timeout']
            fetch['request_timeout'] = fetch['timeout']
            del fetch['timeout']
        if 'data' in fetch:
            fetch['body'] = fetch['data']
            del fetch['data']
        cookie = None
        if 'cookies' in fetch:
            cookie = fetch['cookies']
            del fetch['cookies']

        def handle_response(response):
            response.headers = final_headers
            session.extract_cookies_to_jar(request, cookie_headers)
            if response.error and not isinstance(response.error, tornado.httpclient.HTTPError):
                result = {
                    'status_code': 599,
                    'error': "%r" % response.error,
                    'content': "",
                    'time': time.time() - start_time,
                    'orig_url': url,
                    'url': url,
                }
                callback('http', task, result)
                self.on_result('http', task, result)
                return task, result
            result = {}
            result['orig_url'] = url
            result['content'] = response.body or ''
            result['headers'] = dict(response.headers)
            result['status_code'] = response.code
            result['url'] = response.effective_url or url
            result['cookies'] = session.to_dict()
            result['time'] = time.time() - start_time
            result['save'] = task_fetch.get('save')
            if 200 <= response.code < 300:
                logger.info("[%d] %s %.2fs", response.code, url, result['time'])
            else:
                logger.warning("[%d] %s %.2fs", response.code, url, result['time'])
            callback('http', task, result)
            self.on_result('http', task, result)
            return task, result

        def header_callback(line):
            line = line.strip()
            if line.startswith("HTTP/"):
                final_headers.clear()
                return
            if not line:
                return
            final_headers.parse_line(line)
            cookie_headers.parse_line(line)

        start_time = time.time()
        session = cookie_utils.CookieSession()
        cookie_headers = tornado.httputil.HTTPHeaders()
        final_headers = tornado.httputil.HTTPHeaders()
        try:
            request = tornado.httpclient.HTTPRequest(header_callback=header_callback, **fetch)
            if cookie:
                session.update(cookie)
                if 'Cookie' in request.headers:
                    del request.headers['Cookie']
                request.headers['Cookie'] = session.get_cookie_header(request)
            if self.async:
                self.http_client.fetch(request, handle_response)
            else:
                return handle_response(self.http_client.fetch(request))
        except tornado.httpclient.HTTPError as e:
            return handle_response(e.response)
        except Exception as e:
            raise
            result = {
                'status_code': 599,
                'error': '%r' % e,
                'content': "",
                'time': time.time() - start_time,
                'orig_url': url,
                'url': url,
            }
            logger.error("[599] %s, %r %.2fs", url, e, result['time'])
            callback('http', task, result)
            self.on_result('http', task, result)
            return task, result

    phantomjs_adding_options = ['js_run_at', 'js_script', 'load_images']

    def phantomjs_fetch(self, url, task, callback):
        self.on_fetch('phantomjs', task)
        if not self.phantomjs_proxy:
            result = {
                "orig_url": url,
                "content": "phantomjs is not enabled.",
                "headers": {},
                "status_code": 501,
                "url": url,
                "cookies": {},
                "time": 0,
                "save": task.get('fetch', {}).get('save')
            }
            logger.warning("[501] %s 0s", url)
            callback('http', task, result)
            self.on_result('http', task, result)
            return task, result

        request_conf = {
            'follow_redirects': False
        }

        fetch = dict(self.default_options)
        fetch.setdefault('url', url)
        fetch.setdefault('headers', {})
        task_fetch = task.get('fetch', {})
        fetch.update(task_fetch)
        if 'timeout' in fetch:
            request_conf['connect_timeout'] = fetch['timeout']
            request_conf['request_timeout'] = fetch['timeout']
        fetch['headers'].setdefault('User-Agent', self.user_agent)

        start_time = time.time()

        def handle_response(response):
            if not response:
                result = {
                    'status_code': 599,
                    'error': "599 Timeout error",
                    'content': "",
                    'time': time.time() - start_time,
                    'orig_url': url,
                    'url': url,
                }
            else:
                try:
                    result = json.loads(response.body)
                except Exception as e:
                    result = {
                        'status_code': 599,
                        'error': '%r' % e,
                        'content': '',
                        'time': time.time() - start_time,
                        'orig_url': url,
                        'url': url,
                    }
            if result.get('status_code', 200):
                logger.info("[%d] %s %.2fs", result['status_code'], url, result['time'])
            else:
                logger.exception("[%d] %s, %r %.2fs", result['status_code'],
                                 url, result['content'], result['time'])
            callback('phantomjs', task, result)
            self.on_result('phantomjs', task, result)
            return task, result

        try:
            request = tornado.httpclient.HTTPRequest(
                url="%s" % self.phantomjs_proxy, method="POST",
                body=json.dumps(fetch), **request_conf)
            if self.async:
                self.http_client.fetch(request, handle_response)
            else:
                return handle_response(self.http_client.fetch(request))
        except tornado.httpclient.HTTPError as e:
            return handle_response(e.response)
        except Exception as e:
            result = {
                'status_code': 599,
                'error': "%r" % e,
                'content': '',
                'time': time.time() - start_time,
                'orig_url': url,
                'url': url,
            }
            logger.error("[599] %s, %r %.2fs", url, e, result['time'])
            callback('phantomjs', task, result)
            self.on_result('phantomjs', task, result)
            return task, result

    def run(self):
        def queue_loop():
            if not self.outqueue or not self.inqueue:
                return
            while not self._quit:
                try:
                    if self.outqueue.full():
                        break
                    if self.http_client.free_size() <= 0:
                        break
                    task = self.inqueue.get_nowait()
                    # FIXME: decode unicode_obj should used after data selete from
                    # database, it's used here for performance
                    task = utils.decode_unicode_obj(task)
                    self.fetch(task)
                except Queue.Empty:
                    break
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    logger.exception(e)
                    break

        tornado.ioloop.PeriodicCallback(queue_loop, 100).start()
        self._running = True
        try:
            tornado.ioloop.IOLoop.instance().start()
        except KeyboardInterrupt:
            pass

        logger.info("fetcher exiting...")

    def size(self):
        return self.http_client.size()

    def quit(self):
        self._running = False
        self._quit = True
        tornado.ioloop.IOLoop.instance().stop()

    def xmlrpc_run(self, port=24444, bind='127.0.0.1', logRequests=False):
        import umsgpack
        from SimpleXMLRPCServer import SimpleXMLRPCServer
        from xmlrpclib import Binary

        server = SimpleXMLRPCServer((bind, port), allow_none=True, logRequests=logRequests)
        server.register_introspection_functions()
        server.register_multicall_functions()

        server.register_function(self.quit, '_quit')
        server.register_function(self.size)

        def sync_fetch(task):
            result = self.sync_fetch(task)
            result = Binary(umsgpack.packb(result))
            return result
        server.register_function(sync_fetch, 'fetch')

        def dump_counter(_time, _type):
            return self._cnt[_time].to_dict(_type)
        server.register_function(dump_counter, 'counter')

        server.timeout = 0.5
        while not self._quit:
            server.handle_request()
        server.server_close()

    def on_fetch(self, type, task):
        """type in ('data', 'http')"""
        pass

    def on_result(self, type, task, result):
        """type in ('data', 'http')"""
        status_code = result.get('status_code', 599)
        if status_code != 599:
            status_code = (int(status_code) / 100 * 100)
        self._cnt['5m'].event((task.get('project'), status_code), +1)
        self._cnt['1h'].event((task.get('project'), status_code), +1)

        if type == 'http' and result.get('time'):
            content_len = len(result.get('content', ''))
            self._cnt['5m'].event((task.get('project'), 'speed'),
                                  float(content_len) / result.get('time'))
            self._cnt['1h'].event((task.get('project'), 'speed'),
                                  float(content_len) / result.get('time'))
            self._cnt['5m'].event((task.get('project'), 'time'), result.get('time'))
            self._cnt['1h'].event((task.get('project'), 'time'), result.get('time'))
