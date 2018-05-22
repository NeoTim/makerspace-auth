#!/usr/bin/python
#
# Copyright 2017-2018 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Example using two buttons for "on" and "off" once badged.

"""

import atexit
import os
import sys
import subprocess
import shlex
import ConfigParser

from authbox.api import BaseDispatcher, GPIO
from authbox.config import Config
from authbox.timer import Timer

DEVNULL = open('/dev/null', 'r+')

class Dispatcher(BaseDispatcher):
  def __init__(self, config):
    super(Dispatcher, self).__init__(config)

    self.authorized = False
    self.load_config_object('on_button', on_down=self.on_button_down)
    self.load_config_object('off_button', on_down=self.abort)
    self.load_config_object('badge_reader', on_scan=self.badge_scan)
    self.load_config_object('enable_output')
    self.load_config_object('buzzer')
    self.load_config_object('traffic_light', on_change=self.change_traffic_light)
    self.warning_timer = Timer(self.event_queue, 'warning_timer', self.sound_attention)
    self.expire_timer = Timer(self.event_queue, 'expire_timer', self.abort)
    self.expecting_press_timer = Timer(self.event_queue, 'expecting_press_timer', self.abort)
    # Otherwise, start them manually!
    self.threads.extend([self.warning_timer, self.expire_timer, self.expecting_press_timer])

    self.noise = None

  def _get_command_line(self, section, key, format_args):
    """Constructs a command line, safely.

    The value can contain {key}, {}, and {5} style interpolation:
      - {key} will be resolved in the config.get; those are considered safe and
        spaces will separate args.
      - {} works on each arg independently (probably not what you want).
      - {5} works fine.
    """
    try:
      value = self.config.get(section, key)
    except (ConfigParser.NoSectionError, ConfigParser.NoOptionError) as e:
      return None
    pieces = shlex.split(value)
    return [p.format(*format_args) for p in pieces]

  def badge_scan(self, badge_id):
    # Malicious badge "numbers" that contain spaces require this extra work.
    command = self._get_command_line('auth', 'command', [badge_id])
    # TODO timeout
    # TODO test with missing command
    rc = subprocess.call(command)
    if rc == 0:
      self.authorized = True
      self.badge_id = badge_id
      self.expecting_press_timer.set(
          self.config.get_int_seconds('auth', 'initial_badge_time', '30s'))
      self.sound_chirp()
    else:
      self.sound_deny()

  def on_button_down(self, source):
    print "Button down", source
    if not self.authorized:
      self.sound_deny()
      return
    self.expecting_press_timer.cancel()
    self.on_button.on()
    self.enable_output.on()
    self.warning_timer.cancel()
    self.expire_timer.cancel()
    # TODO use extend time if we were already enabled, and run its command for
    # logging.
    # N.b. Duration (or extend) includes the warning time.
    self.warning_timer.set(self.config.get_int_seconds('auth', 'duration', '5m') -
                           self.config.get_int_seconds('auth', 'warning', '10s'))
    self.expire_timer.set(self.config.get_int_seconds('auth', 'duration', '5m'))
    self.sound_stop()

  def abort(self, source):
    print "Abort", source
    if self.authorized:
      command = self._get_command_line('auth', 'deauth_command', [self.badge_id])
      subprocess.call(command)
    self.authorized = False
    self.warning_timer.cancel()
    self.expecting_press_timer.cancel()
    self.on_button.off()
    self.enable_output.off()
    self.sound_stop()

  def change_traffic_light(self, source):
    command = self._get_command_line('auth', 'status_command', [source.state])
    subprocess.call(command)

  def sound_deny(self):
    if self.buzzer:
      self.buzzer.beep()
    if self.noise:
      self.noise.kill()
      self.noise = None
    if self.config.get('sounds', 'enable', default='0') == '1':
      sound_command = self._get_command_line('sounds', 'command', [self.config.get('sounds', 'deny_filename')])
      self.noise = subprocess.Popen(sound_command, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)

  def sound_chirp(self):
    if self.buzzer:
      self.buzzer.beep()
    if self.noise:
      self.noise.kill()
      self.noise = None
    if self.config.get('sounds', 'enable', default='0') == '1':
      sound_command = self._get_command_line('sounds', 'command', [self.config.get('sounds', 'chirp_filename')])
      self.noise = subprocess.Popen(sound_command, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)
    self.on_button.blink()

  def sound_attention(self, unused_source=None):
    if self.buzzer:
      self.buzzer.beepbeep()
    if self.noise:
      self.noise.kill()
      self.noise = None
    if self.config.get('sounds', 'enable', default='0') == '1':
      sound_command = self._get_command_line('sounds', 'command', [self.config.get('sounds', 'attention_filename')])
      self.noise = subprocess.Popen(sound_command, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)
    self.on_button.blink()

  def sound_stop(self):
    if self.buzzer:
      self.buzzer.off()
    if self.noise:
      self.noise.kill()
      self.noise = None


def main(args):
  atexit.register(GPIO.cleanup)

  if not args:
    root = '~'
  else:
    root = args[0]

  config = Config(os.path.join(root, '.authboxrc'))
  Dispatcher(config).run_loop()

if __name__ == '__main__':
  main(sys.argv[1:])
