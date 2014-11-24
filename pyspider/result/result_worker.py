#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Author: Binux<i@binux.me>
#         http://binux.me
# Created on 2014-10-19 15:37:46

import Queue
import logging
logger = logging.getLogger("result")


class ResultWorker(object):

    """
    do with result
    override this if needed.
    """

    def __init__(self, resultdb, inqueue):
        self.resultdb = resultdb
        self.inqueue = inqueue
        self._quit = False

    def on_result(self, task, result):
        if not result:
            return
        assert 'taskid' in task, 'need taskid in task'
        assert 'project' in task, 'need project in task'
        assert 'url' in task, 'need url in task'
        return self.resultdb.save(
            project=task['project'],
            taskid=task['taskid'],
            url=task['url'],
            result=result
        )

    def quit(self):
        self._quit = True

    def run(self):
        while not self._quit:
            try:
                task, result = self.inqueue.get(timeout=1)
                if 'taskid' in task and 'project' in task and 'url' in task:
                    logger.info('result %s:%s %s -> %.30r' % (
                        task['project'], task['taskid'], task['url'], result))
                else:
                    logger.warning('result UNKNOW -> %.30r' % result)
                self.on_result(task, result)
            except Queue.Empty as e:
                continue
            except KeyboardInterrupt:
                break
            except AssertionError as e:
                logger.error(e)
                continue
            except Exception as e:
                logger.exception(e)
                continue

        logger.info("result_worker exiting...")
