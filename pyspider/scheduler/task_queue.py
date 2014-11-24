#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Author: Binux<i@binux.me>
#         http://binux.me
# Created on 2014-02-07 13:12:10

import time
import heapq
import Queue
import logging
import threading
from UserDict import DictMixin
from token_bucket import Bucket


class InQueueTask(DictMixin):
    __slots__ = ('taskid', 'priority', 'exetime')
    __getitem__ = lambda *x: getattr(*x)
    __setitem__ = lambda *x: setattr(*x)
    keys = lambda self: self.__slots__

    def __init__(self, taskid, priority=0, exetime=0):
        self.taskid = taskid
        self.priority = priority
        self.exetime = exetime

    def __cmp__(self, other):
        if self.exetime == 0 and other.exetime == 0:
            return -cmp(self.priority, other.priority)
        else:
            return cmp(self.exetime, other.exetime)


class PriorityTaskQueue(Queue.Queue):

    '''
    TaskQueue
    '''

    def _init(self, maxsize):
        self.queue = []
        self.queue_dict = dict()

    def _qsize(self, len=len):
        return len(self.queue)

    def _put(self, item, heappush=heapq.heappush):
        heappush(self.queue, item)
        self.queue_dict[item.taskid] = item

    def _get(self, heappop=heapq.heappop):
        item = heappop(self.queue)
        self.queue_dict.pop(item.taskid, None)
        return item

    @property
    def top(self):
        return self.queue[0]

    def resort(self):
        self.mutex.acquire()
        heapq.heapify(self.queue)
        self.mutex.release()

    def __contains__(self, taskid):
        return taskid in self.queue_dict

    def __getitem__(self, taskid):
        return self.queue_dict[taskid]

    def __setitem__(self, taskid, item):
        assert item.taskid == taskid
        self.put(item)


class TaskQueue(object):

    '''
    task queue for scheduler, have a priority queue and a time queue for delayed tasks
    '''
    processing_timeout = 10 * 60

    def __init__(self, rate=0, burst=0):
        self.mutex = threading.Lock()
        self.priority_queue = PriorityTaskQueue()
        self.time_queue = PriorityTaskQueue()
        self.processing = PriorityTaskQueue()
        self.bucket = Bucket(rate=rate, burst=burst)

    @property
    def rate(self):
        return self.bucket.rate

    @rate.setter
    def rate(self, value):
        self.bucket.rate = value

    @property
    def burst(self):
        return self.burst.burst

    @burst.setter
    def burst(self, value):
        self.bucket.burst = value

    def check_update(self):
        self._check_time_queue()
        self._check_processing()

    def _check_time_queue(self):
        now = time.time()
        self.mutex.acquire()
        while self.time_queue.qsize() and self.time_queue.top.exetime < now:
            task = self.time_queue.get()
            task.exetime = 0
            self.priority_queue.put(task)
        self.mutex.release()

    def _check_processing(self):
        now = time.time()
        self.mutex.acquire()
        while self.processing.qsize() and self.processing.top.exetime < now:
            task = self.processing.get()
            if task.taskid is None:
                continue
            task.exetime = 0
            self.priority_queue.put(task)
            logging.info("[processing: retry] %s" % task.taskid)
        self.mutex.release()

    def put(self, taskid, priority=0, exetime=0):
        now = time.time()
        self.mutex.acquire()
        if taskid in self.priority_queue:
            task = self.priority_queue[taskid]
            if priority > task.priority:
                task.priority = priority
        elif taskid in self.time_queue:
            task = self.time_queue[taskid]
            if priority > task.priority:
                task.priority = priority
            if exetime < task.exetime:
                task.exetime = exetime
        else:
            task = InQueueTask(taskid, priority)
            if exetime and exetime > now:
                task.exetime = exetime
                self.time_queue.put(task)
            else:
                self.priority_queue.put(task)
        self.mutex.release()

    def get(self):
        if self.bucket.get() < 1:
            return None
        now = time.time()
        self.mutex.acquire()
        try:
            task = self.priority_queue.get_nowait()
            self.bucket.desc()
        except Queue.Empty:
            self.mutex.release()
            return None
        task.exetime = now + self.processing_timeout
        self.processing.put(task)
        self.mutex.release()
        return task.taskid

    def done(self, taskid):
        if taskid in self.processing:
            self.processing[taskid].taskid = None

    def __len__(self):
        return self.priority_queue.qsize() + self.time_queue.qsize()

    def __contains__(self, taskid):
        if taskid in self.priority_queue or taskid in self.time_queue:
            return True
        if taskid in self.processing and self.processing[taskid].taskid:
            return True
        return False


if __name__ == '__main__':
    task_queue = TaskQueue()
    task_queue.processing_timeout = 0.1
    task_queue.put('a3', 3, time.time() + 0.1)
    task_queue.put('a1', 1)
    task_queue.put('a2', 2)
    assert task_queue.get() == 'a2'
    time.sleep(0.1)
    task_queue._check_time_queue()
    assert task_queue.get() == 'a3'
    assert task_queue.get() == 'a1'
    task_queue._check_processing()
    assert task_queue.get() == 'a2'
    assert len(task_queue) == 0
