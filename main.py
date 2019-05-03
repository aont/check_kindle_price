#!/usr/bin/env python2
# -*- coding: utf-8 -*-

import io
import os
import sys

import requests

class LINE(object):
    def __init__(self, sess, line_notify_token):
        self.sess = sess
        self.line_notify_token = line_notify_token
        self.line_notify_api = 'https://notify-api.line.me/api/notify'
        self.headers = {'Authorization': 'Bearer ' + line_notify_token}

    def notify(self, message):
        line_notify = self.sess.post(self.line_notify_api, data = {'message': message}, headers = self.headers)

if __name__ == '__main__':
    sess = requests.session()
    line_notify_token = os.environ['LINE_TOKEN']
    line = LINE(sess, line_notify_token)
    line.notify('test')

    
