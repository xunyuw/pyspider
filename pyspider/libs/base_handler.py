#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Author: Binux<i@binux.me>
#         http://binux.me
# Created on 2014-02-16 23:12:48

import sys
import inspect
import functools
import fractions
from pyspider.libs.log import LogFormatter
from pyspider.libs.url import quote_chinese, _build_url, _encode_params
from pyspider.libs.utils import md5string, hide_me
from pyspider.libs.ListIO import ListO
from pyspider.libs.response import rebuild_response
from pyspider.libs.pprint import pprint


class ProcessorResult(object):

    def __init__(self, result, follows, messages, logs, exception, extinfo):
        self.result = result
        self.follows = follows
        self.messages = messages
        self.logs = logs
        self.exception = exception
        self.extinfo = extinfo

    def rethrow(self):
        if self.exception:
            raise self.exception

    def logstr(self):
        result = []
        formater = LogFormatter(color=False)
        for record in self.logs:
            if isinstance(record, basestring):
                result.append(record)
                continue
            else:
                if record.exc_info:
                    a, b, tb = record.exc_info
                    tb = hide_me(tb, globals())
                    record.exc_info = a, b, tb
                result.append(formater.format(record))
                result.append('\n')
        ret = ''.join(result)
        if isinstance(ret, unicode):
            return ret
        else:
            try:
                return ret.decode('utf8')
            except UnicodeDecodeError:
                return repr(ret)


def catch_status_code_error(func):
    func._catch_status_code_error = True
    return func


def not_send_status(func):
    @functools.wraps(func)
    def wrapper(self, response, task):
        self._extinfo['not_send_status'] = True
        function = func.__get__(self, self.__class__)
        return self._run_func(function, response, task)
    return wrapper


def config(_config=None, **kwargs):
    if _config is None:
        _config = {}
    _config.update(kwargs)

    def wrapper(func):
        func._config = _config
        return func
    return wrapper


class NOTSET(object):
    pass


def every(minutes=NOTSET, seconds=NOTSET):
    def wrapper(func):
        @functools.wraps(func)
        def on_cronjob(self, response, task):
            if (
                    response.save
                    and 'tick' in response.save
                    and response.save['tick'] % (minutes * 60 + seconds) != 0
            ):
                return None
            function = func.__get__(self, self.__class__)
            return self._run_func(function, response, task)
        on_cronjob.is_cronjob = True
        on_cronjob.tick = minutes * 60 + seconds
        return on_cronjob

    if inspect.isfunction(minutes):
        func = minutes
        minutes = 1
        seconds = 0
        return wrapper(func)

    if minutes is NOTSET:
        if seconds is NOTSET:
            minutes = 1
            seconds = 0
        else:
            minutes = 0
    if seconds is NOTSET:
        seconds = 0

    return wrapper


class BaseHandlerMeta(type):

    def __new__(cls, name, bases, attrs):
        cron_jobs = []
        min_tick = 0

        for each in attrs.values():
            if inspect.isfunction(each) and getattr(each, 'is_cronjob', False):
                cron_jobs.append(each)
                min_tick = fractions.gcd(min_tick, each.tick)
        newcls = type.__new__(cls, name, bases, attrs)
        newcls.cron_jobs = cron_jobs
        newcls.min_tick = min_tick
        return newcls


class BaseHandler(object):
    __metaclass__ = BaseHandlerMeta
    cron_jobs = []
    min_tick = 0

    def _reset(self):
        self._extinfo = {}
        self._messages = []
        self._follows = []

    def _run_func(self, function, *arguments):
        args, varargs, keywords, defaults = inspect.getargspec(function)
        return function(*arguments[:len(args) - 1])

    def _run(self, task, response):
        self._reset()
        if isinstance(response, dict):
            response = rebuild_response(response)
        process = task.get('process', {})
        callback = process.get('callback', '__call__')
        if not hasattr(self, callback):
            raise NotImplementedError("self.%s() not implemented!" % callback)

        function = getattr(self, callback)
        if not getattr(function, '_catch_status_code_error', False):
            response.raise_for_status()
        return self._run_func(function, response, task)

    def run(self, module, task, response):
        logger = module.logger
        result = None
        exception = None
        stdout = sys.stdout
        self.task = task
        self.response = response

        try:
            sys.stdout = ListO(module.log_buffer)
            if inspect.isgeneratorfunction(self._run):
                for result in self._run(task, response):
                    self._run_func(self.on_result, result, response, task)
            else:
                result = self._run(task, response)
                self._run_func(self.on_result, result, response, task)
        except Exception as e:
            logger.exception(e)
            exception = e
        finally:
            self.task = None
            self.response = None
            sys.stdout = stdout
            follows = self._follows
            messages = self._messages
            logs = list(module.log_buffer)
            extinfo = self._extinfo

        module.log_buffer[:] = []
        return ProcessorResult(result, follows, messages, logs, exception, extinfo)

    def _crawl(self, url, **kwargs):
        task = {}

        if kwargs.get('callback'):
            callback = kwargs['callback']
            if isinstance(callback, basestring) and hasattr(self, callback):
                func = getattr(self, callback)
            elif hasattr(callback, 'im_self') and callback.im_self is self:
                func = callback
                kwargs['callback'] = func.__name__
            else:
                raise NotImplementedError("self.%s() not implemented!" % callback)
            if hasattr(func, '_config'):
                for k, v in func._config.iteritems():
                    kwargs.setdefault(k, v)

        if hasattr(self, 'crawl_config'):
            for k, v in self.crawl_config.iteritems():
                kwargs.setdefault(k, v)

        url = quote_chinese(_build_url(url.strip(), kwargs.get('params')))
        if kwargs.get('files'):
            assert isinstance(
                kwargs.get('data', {}), dict), "data must be a dict when using with files!"
            content_type, data = _encode_multipart_formdata(kwargs.get('data', {}),
                                                            kwargs.get('files', {}))
            kwargs.setdefault('headers', {})
            kwargs['headers']['Content-Type'] = content_type
            kwargs['data'] = data
        if kwargs.get('data'):
            kwargs['data'] = _encode_params(kwargs['data'])
        if kwargs.get('data'):
            kwargs.setdefault('method', 'POST')

        schedule = {}
        for key in ('priority', 'retries', 'exetime', 'age', 'itag', 'force_update'):
            if key in kwargs and kwargs[key] is not None:
                schedule[key] = kwargs[key]
        if schedule:
            task['schedule'] = schedule

        fetch = {}
        for key in (
                'method',
                'headers',
                'data',
                'timeout',
                'allow_redirects',
                'cookies',
                'proxy',
                'etag',
                'last_modifed',
                'save',
                'js_run_at',
                'js_script',
                'load_images',
                'fetch_type'
        ):
            if key in kwargs and kwargs[key] is not None:
                fetch[key] = kwargs[key]
        if fetch:
            task['fetch'] = fetch

        process = {}
        for key in ('callback', ):
            if key in kwargs and kwargs[key] is not None:
                process[key] = kwargs[key]
        if process:
            task['process'] = process

        task['project'] = self.project_name
        task['url'] = url
        task['taskid'] = task.get('taskid') or md5string(url)

        self._follows.append(task)
        return task

    # apis
    def crawl(self, url, **kwargs):
        '''
        params:
          url
          callback

          method
          params
          data
          files
          headers
          timeout
          allow_redirects
          cookies
          proxy
          etag
          last_modifed

          fetch_type
          js_run_at
          js_script
          load_images

          priority
          retries
          exetime
          age
          itag

          save
          taskid
        '''

        if isinstance(url, basestring):
            return self._crawl(url, **kwargs)
        elif hasattr(url, "__iter__"):
            result = []
            for each in url:
                result.append(self._crawl(each, **kwargs))
            return result

    def is_debugger(self):
        return self.__env__.get('debugger')

    def send_message(self, project, msg, url='data:,on_message'):
        self._messages.append((project, msg, url))

    def on_message(self, project, msg):
        pass

    def on_result(self, result):
        if not result:
            return
        assert self.task, "on_result can't outside a callback."
        if self.is_debugger():
            pprint(result)
        if self.__env__.get('result_queue'):
            self.__env__['result_queue'].put((self.task, result))

    @not_send_status
    def _on_message(self, response):
        project, msg = response.save
        return self.on_message(project, msg)

    @not_send_status
    def _on_cronjob(self, response, task):
        for cronjob in self.cron_jobs:
            function = cronjob.__get__(self, self.__class__)
            self._run_func(function, response, task)

    @not_send_status
    def _on_get_info(self, response, task):
        result = {}
        assert response.save
        for each in response.save:
            if each == 'min_tick':
                result[each] = self.min_tick
        self.crawl('data:,on_get_info', save=result)
