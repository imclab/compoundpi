# vim: set et sw=4 sts=4 fileencoding=utf-8:
#
# Copyright 2014 Dave Hughes <dave@waveform.org.uk>.
#
# This file is part of compoundpi.
#
# compoundpi is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# compoundpi is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# compoundpi.  If not, see <http://www.gnu.org/licenses/>.

"A project for controlling multiple Pi camera modules simultaneously"

from __future__ import (
    unicode_literals,
    absolute_import,
    print_function,
    division,
    )
str = type('')


import sys
import os
import io
import time
import signal

import pytest
from mock import Mock, MagicMock, patch, sentinel

# Several of the modules that CompoundPiServer relies upon are Raspberry Pi
# specific (can't be installed on other platforms) so we need to mock them
# out before performing attempting to import compoundpi.server
daemon_mock = Mock()
runner_mock = Mock()
picamera_mock = Mock()
rpi_mock = Mock()
gpio_mock = Mock()
with patch.dict('sys.modules', {
        'daemon':        daemon_mock,
        'daemon.runner': runner_mock,
        'picamera':      picamera_mock,
        'RPi':           rpi_mock,
        'RPi.GPIO':      gpio_mock,
    }):
    import compoundpi
    import compoundpi.common
    import compoundpi.server
    import compoundpi.exc

    def test_service():
        assert compoundpi.server.service('5000') == 5000
        with patch('socket.getservbyname') as m:
            m.return_value = 22
            assert compoundpi.server.service('ssh') == 22

    def test_address():
        with patch('socket.getaddrinfo') as m:
            m.return_value = [(2, 2, 17, '', ('127.0.0.1', 0))]
            assert compoundpi.server.address('localhost') == '127.0.0.1'

    def test_user():
        assert compoundpi.server.user('1000') == 1000
        with patch('pwd.getpwnam') as m:
            m.return_value = Mock()
            m.return_value.pw_uid = 0
            assert compoundpi.server.user('root') == 0

    def test_group():
        assert compoundpi.server.group('1000') == 1000
        with patch('grp.getgrnam') as m:
            m.return_value = Mock()
            m.return_value.gr_gid = 0
            assert compoundpi.server.group('wheel') == 0

    def test_server_showwarning():
        with patch.object(compoundpi.server.logging, 'warning') as m:
            app = compoundpi.server.CompoundPiServer()
            app.showwarning('foo', Warning, 'foo.py', 1)
            m.assert_called_once_with('foo')

    def test_server_init():
        daemon_mock.runner.make_pidlockfile.return_value = sentinel.pidfile
        daemon_mock.runner.is_pidfile_stale.return_value = False
        with patch.object(daemon_mock, 'DaemonContext') as ctx:
            ctx.__enter__ = Mock()
            ctx.__exit__ = Mock()
            with patch.object(compoundpi.server, 'CompoundPiUDPServer') as srv:
                app = compoundpi.server.CompoundPiServer()
                app([])
                ctx.assert_called_once_with(
                    detach_process=False,
                    stderr=sys.stderr,
                    uid=os.getuid(),
                    gid=os.getgid(),
                    files_preserve=[app.server.socket],
                    pidfile=sentinel.pidfile,
                    signal_map={
                        signal.SIGTERM: app.terminate,
                        signal.SIGINT:  app.interrupt,
                        })
                app.server.serve_forever.assert_called_once_with()

    def test_server_log_files_preserved(tmpdir):
        with patch.object(daemon_mock, 'DaemonContext') as ctx:
            ctx.__enter__ = Mock()
            ctx.__exit__ = Mock()
            with patch.object(compoundpi.server, 'CompoundPiUDPServer') as srv:
                app = compoundpi.server.CompoundPiServer()
                log_filename = os.path.join(str(tmpdir), 'log.txt')
                app(['--log-file', log_filename])
                assert len([
                    fd for fd in ctx.call_args[1]['files_preserve']
                    if hasattr(fd, 'name') and fd.name == log_filename
                    ]) == 1

    def test_server_pidfile_locked():
        pidfile = Mock()
        daemon_mock.runner.make_pidlockfile.return_value = pidfile
        daemon_mock.runner.is_pidfile_stale.return_value = True
        with patch.object(daemon_mock, 'DaemonContext') as ctx:
            ctx.__enter__ = Mock()
            ctx.__exit__ = Mock()
            with patch.object(compoundpi.server, 'CompoundPiUDPServer') as srv:
                app = compoundpi.server.CompoundPiServer()
                app([])
                assert pidfile.break_lock.called_once_with()

    def test_server_thread_join():
        daemon_mock.runner.is_pidfile_stale.return_value = False
        with patch.object(daemon_mock, 'DaemonContext') as ctx:
            ctx.__enter__ = Mock()
            ctx.__exit__ = Mock()
            with patch.object(compoundpi.server, 'CompoundPiUDPServer') as srv:
                with patch('threading.Thread') as thread:
                    thread.return_value = Mock()
                    thread.return_value.is_alive.return_value = True
                    thread.return_value.join.side_effect = RuntimeError('foo')
                    with pytest.raises(RuntimeError):
                        app = compoundpi.server.CompoundPiServer()
                        app([])
                    thread.return_value.start.assert_called_once_with()
                    thread.return_value.is_alive.assert_called_once_with()
                    thread.return_value.join.assert_called_once_with(1)

    def test_server_terminate():
        app = compoundpi.server.CompoundPiServer()
        app.server = Mock()
        app.terminate(signal.SIGTERM, None)
        app.server.shutdown.assert_called_once_with()

    def test_server_interrupt():
        app = compoundpi.server.CompoundPiServer()
        app.server = Mock()
        app.interrupt(signal.SIGINT, None)
        app.server.shutdown.assert_called_once_with()

    def test_handler_bad_request():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            handler = compoundpi.server.CameraRequestHandler(
                    (b'FOO', socket), ('localhost', 1), MagicMock())
            assert m.call_count == 1
            args, kwargs = m.call_args
            assert args[0] == socket
            assert args[1] == ('localhost', 1)
            assert args[2].startswith('0 ERROR\n')

    def test_handler_unknown_command():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            handler = compoundpi.server.CameraRequestHandler(
                    (b'0 FOO', socket), ('localhost', 1), MagicMock())
            assert m.call_count == 1
            args, kwargs = m.call_args
            assert args[0] == socket
            assert args[1] == ('localhost', 1)
            assert args[2].startswith('0 ERROR\n')

    def test_handler_unknown_command_with_params():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            handler = compoundpi.server.CameraRequestHandler(
                    (b'0 FOO 1 2 3', socket), ('localhost', 1), MagicMock())
            assert m.call_count == 1
            args, kwargs = m.call_args
            assert args[0] == socket
            assert args[1] == ('localhost', 1)
            assert args[2].startswith('0 ERROR\n')

    def test_handler_invalid_client():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            with patch.object(compoundpi.server.warnings, 'warn') as w:
                socket = Mock()
                server = MagicMock()
                server.client_address = ('foo', 1)
                compoundpi.server.CameraRequestHandler(
                        (b'0 LIST', socket), ('localhost', 1), server)
                assert w.call_count == 1
                assert isinstance(
                        w.call_args[0][0], compoundpi.exc.CompoundPiInvalidClient)

    def test_handler_stale_seqno():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            with patch.object(compoundpi.server.warnings, 'warn') as w:
                socket = Mock()
                server = MagicMock()
                server.seqno = 10
                server.client_address = ('localhost', 1)
                compoundpi.server.CameraRequestHandler(
                        (b'0 LIST', socket), ('localhost', 1), server)
                assert w.call_count == 1
                assert isinstance(
                        w.call_args[0][0], compoundpi.exc.CompoundPiStaleSequence)

    def test_ack_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            responder = Mock()
            server.responders = {(('localhost', 1), 0): responder}
            handler = compoundpi.server.CameraRequestHandler(
                    (b'0 ACK', socket), ('localhost', 1), server)
            assert responder.terminate == True
            assert responder.join.called_once_with()

    def test_hello_handler_stale_time():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            with patch.object(compoundpi.server.warnings, 'warn') as w:
                socket = Mock()
                server = MagicMock()
                server.client_address = ('localhost', 1)
                server.client_timestamp = 2000.0
                handler = compoundpi.server.CameraRequestHandler(
                        (b'0 HELLO 1000.0', socket), ('localhost', 1), server)
                assert w.call_count == 1
                assert isinstance(
                        w.call_args[0][0], compoundpi.exc.CompoundPiStaleClientTime)

    def test_hello_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = None
            handler = compoundpi.server.CameraRequestHandler(
                    (b'0 HELLO 1000.0', socket), ('localhost', 1), server)
            assert server.client_address == ('localhost', 1)
            assert server.client_timestamp == 1000.0
            assert server.seqno == 0
            m.assert_called_once_with(
                    socket, ('localhost', 1),
                    '0 OK\nVERSION %s' % compoundpi.__version__)

    def test_blink_thread():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            with patch.object(compoundpi.server.time, 'sleep') as sleep:
                server = MagicMock()
                handler = compoundpi.server.CameraRequestHandler(
                        (b'1 BLINK', Mock()), ('localhost', 1), server)
                start = time.time()
                handler.blink_led(0.1)
                assert time.time() - start >= 0.1
                assert sleep.call_count >= 2
                assert server.camera.led == True

    def test_blink_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            with patch('threading.Thread') as thread:
                socket = Mock()
                server = MagicMock()
                server.client_address = ('localhost', 1)
                server.seqno = 0
                handler = compoundpi.server.CameraRequestHandler(
                        (b'1 BLINK', socket), ('localhost', 1), server)
                assert server.seqno == 1
                m.assert_called_once_with(
                        socket, ('localhost', 1), '1 OK\n')
                thread.assert_called_once_with(target=handler.blink_led, args=(5,))

    def test_status_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            with patch.object(compoundpi.server.time, 'time') as now:
                socket = Mock()
                server = MagicMock()
                server.client_address = ('localhost', 1)
                server.seqno = 1
                server.camera.resolution = (1280, 720)
                server.camera.framerate = 30
                server.camera.awb_mode = 'auto'
                server.camera.awb_gains = (1.5, 1.3)
                server.camera.exposure_mode = 'off'
                server.camera.exposure_speed = 100000
                server.camera.exposure_compensation = 0
                server.camera.iso = 100
                server.camera.meter_mode = 'spot'
                server.camera.brightness = 50
                server.camera.contrast = 25
                server.camera.saturation = 15
                server.camera.hflip = True
                server.camera.vflip = False
                server.images = []
                now.return_value = 2000.0
                handler = compoundpi.server.CameraRequestHandler(
                        (b'2 STATUS', socket), ('localhost', 1), server)
                assert server.seqno == 2
                m.assert_called_once_with(
                        socket, ('localhost', 1),
                        '2 OK\n'
                        'RESOLUTION 1280 720\n'
                        'FRAMERATE 30\n'
                        'AWB auto 1.5 1.3\n'
                        'EXPOSURE off 100.0 0\n'
                        'ISO 100\n'
                        'METERING spot\n'
                        'LEVELS 50 25 15\n'
                        'FLIP 1 0\n'
                        'TIMESTAMP 2000.0\n'
                        'IMAGES 0\n')

    def test_resolution_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = ('localhost', 1)
            server.seqno = 1
            handler = compoundpi.server.CameraRequestHandler(
                    (b'2 RESOLUTION 1920 1080', socket), ('localhost', 1), server)
            assert server.seqno == 2
            assert server.camera.resolution == (1920, 1080)
            m.assert_called_once_with(
                    socket, ('localhost', 1),
                    '2 OK\n')

    def test_framerate_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = ('localhost', 1)
            server.seqno = 1
            handler = compoundpi.server.CameraRequestHandler(
                    (b'2 FRAMERATE 30/2', socket), ('localhost', 1), server)
            assert server.seqno == 2
            assert server.camera.framerate == 15
            m.assert_called_once_with(
                    socket, ('localhost', 1),
                    '2 OK\n')

    def test_awb_handler_auto():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = ('localhost', 1)
            server.seqno = 1
            handler = compoundpi.server.CameraRequestHandler(
                    (b'2 AWB auto 1.0 1.0', socket), ('localhost', 1), server)
            assert server.seqno == 2
            assert server.camera.awb_mode == 'auto'
            m.assert_called_once_with(
                    socket, ('localhost', 1),
                    '2 OK\n')

    def test_awb_handler_manual():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = ('localhost', 1)
            server.seqno = 1
            handler = compoundpi.server.CameraRequestHandler(
                    (b'2 AWB off 1.5 1.3', socket), ('localhost', 1), server)
            assert server.seqno == 2
            assert server.camera.awb_mode == 'off'
            assert server.camera.awb_gains == (1.5, 1.3)
            m.assert_called_once_with(
                    socket, ('localhost', 1),
                    '2 OK\n')

    def test_exposure_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = ('localhost', 1)
            server.seqno = 1
            handler = compoundpi.server.CameraRequestHandler(
                    (b'2 EXPOSURE auto 20.0 24', socket), ('localhost', 1), server)
            assert server.seqno == 2
            assert server.camera.exposure_mode == 'auto'
            assert server.camera.shutter_speed == 20000.0
            assert server.camera.exposure_compensation == 24
            m.assert_called_once_with(
                    socket, ('localhost', 1),
                    '2 OK\n')

    def test_metering_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = ('localhost', 1)
            server.seqno = 1
            handler = compoundpi.server.CameraRequestHandler(
                    (b'2 METERING spot', socket), ('localhost', 1), server)
            assert server.seqno == 2
            assert server.camera.meter_mode == 'spot'
            m.assert_called_once_with(
                    socket, ('localhost', 1),
                    '2 OK\n')

    def test_iso_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = ('localhost', 1)
            server.seqno = 1
            handler = compoundpi.server.CameraRequestHandler(
                    (b'2 ISO 400', socket), ('localhost', 1), server)
            assert server.seqno == 2
            assert server.camera.iso == 400
            m.assert_called_once_with(
                    socket, ('localhost', 1),
                    '2 OK\n')

    def test_levels_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = ('localhost', 1)
            server.seqno = 1
            handler = compoundpi.server.CameraRequestHandler(
                    (b'2 LEVELS 1 2 3', socket), ('localhost', 1), server)
            assert server.seqno == 2
            assert server.camera.brightness == 1
            assert server.camera.contrast == 2
            assert server.camera.saturation == 3
            m.assert_called_once_with(
                    socket, ('localhost', 1),
                    '2 OK\n')

    def test_flip_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = ('localhost', 1)
            server.seqno = 1
            handler = compoundpi.server.CameraRequestHandler(
                    (b'2 FLIP 1 0', socket), ('localhost', 1), server)
            assert server.seqno == 2
            assert server.camera.hflip == True
            assert server.camera.vflip == False
            m.assert_called_once_with(
                    socket, ('localhost', 1),
                    '2 OK\n')

    def test_stream_generator():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            with patch.object(compoundpi.server.time, 'time') as now:
                with patch.object(compoundpi.server.io, 'BytesIO') as stream:
                    server = MagicMock()
                    server.images = []
                    now.return_value = 100.0
                    stream.return_value = sentinel.stream
                    handler = compoundpi.server.CameraRequestHandler(
                            (b'2 ACK', Mock()), ('localhost', 1), server)
                    for s in handler.stream_generator(2):
                        assert s is sentinel.stream
                    assert server.images == [
                        (100.0, sentinel.stream),
                        (100.0, sentinel.stream),
                        ]

    def test_capture_handler():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            socket = Mock()
            server = MagicMock()
            server.client_address = ('localhost', 1)
            server.seqno = 1
            with patch.object(compoundpi.server.CameraRequestHandler, 'stream_generator') as gen:
                gen.return_value = sentinel.iterator
                handler = compoundpi.server.CameraRequestHandler(
                        (b'2 CAPTURE 1 1', socket), ('localhost', 1), server)
                assert server.seqno == 2
                assert server.camera.led == True
                server.camera.capture_sequence.assert_called_once_with(
                        sentinel.iterator, format='jpeg', use_video_port=True)
                m.assert_called_once_with(
                        socket, ('localhost', 1),
                        '2 OK\n')

    def test_capture_handler_with_sync():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            with patch.object(compoundpi.server.time, 'time') as now:
                with patch.object(compoundpi.server.time, 'sleep') as sleep:
                    socket = Mock()
                    server = MagicMock()
                    server.client_address = ('localhost', 1)
                    server.seqno = 1
                    now.return_value = 1000.0
                    with patch.object(compoundpi.server.CameraRequestHandler, 'stream_generator') as gen:
                        gen.return_value = sentinel.iterator
                        handler = compoundpi.server.CameraRequestHandler(
                                (b'2 CAPTURE 1 0 1050.0', socket), ('localhost', 1), server)
                        assert server.seqno == 2
                        assert server.camera.led == True
                        sleep.assert_called_once_with(50.0)
                        server.camera.capture_sequence.assert_called_once_with(
                                sentinel.iterator, format='jpeg', use_video_port=False)
                        m.assert_called_once_with(
                                socket, ('localhost', 1),
                                '2 OK\n')

    def test_capture_handler_past_sync():
        with patch.object(compoundpi.server, 'NetworkRepeater') as m:
            with patch.object(compoundpi.server.time, 'time') as now:
                with patch.object(compoundpi.server.time, 'sleep') as sleep:
                    socket = Mock()
                    server = MagicMock()
                    server.client_address = ('localhost', 1)
                    server.seqno = 1
                    now.return_value = 1000.0
                    with patch.object(compoundpi.server.CameraRequestHandler, 'stream_generator') as gen:
                        handler = compoundpi.server.CameraRequestHandler(
                                (b'2 CAPTURE 1 0 900.0', socket), ('localhost', 1), server)
                        assert m.call_count == 1
                        args, kwargs = m.call_args
                        assert args[0] == socket
                        assert args[1] == ('localhost', 1)
                        assert args[2].startswith('2 ERROR\n')
