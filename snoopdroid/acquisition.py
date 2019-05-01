# -*- coding: utf-8 -*-
#
# Snoopdroid
#
# Copyright (C) 2019 Claudio Guarnieri <https://nex.sx>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import os
import sys
import time
import shutil
from usb1 import USBErrorBusy

from adb import adb_commands
from adb import sign_pythonrsa
from adb.usb_exceptions import DeviceAuthError

from .ui import PullProgress, info, highlight, error
from .utils import get_sha256

class Package(object):
    def __init__(self, name, files=None):
        self.name = name
        self.files = files or []

class Acquisition(object):
    def __init__(self, storage_folder=None):
        self.device = None
        self.packages = []
        self.storage_folder = storage_folder

    def __clean_output(self, output):
        return output.strip().replace("package:", "")

    def connect(self):
        # Maybe one day they will merge:
        # https://github.com/google/python-adb/pull/142
        priv_key_path = os.path.expanduser('~/.android/adbkey')
        with open(priv_key_path, "rb") as handle:
            priv_key = handle.read()
        pub_key_path = priv_key_path + ".pub"
        with open(pub_key_path, "rb") as handle:
            pub_key = handle.read()

        signer = sign_pythonrsa.PythonRSASigner(pub_key, priv_key)
        self.device = adb_commands.AdbCommands()

        while True:
            try:
                self.device.ConnectDevice(rsa_keys=[signer])
            except USBErrorBusy:
                print(error("Device is busy, maybe run `adb kill-server` and try again."))
                sys.exit(-1)
            except DeviceAuthError:
                print(error("You need to authorize this computer on the Android device. Retrying in 5 seconds..."))
                time.sleep(5)
            except Exception as e:
                print(error(repr(e)))
                sys.exit(-1)
            else:
                break

    def disconnect(self):
        self.device.Close()

    def reconnect(self):
        print(info("Reconnecting ..."))
        self.disconnect()
        self.connect()

    def get_packages(self):
        print(info("Retrieving package names ..."))

        output = self.device.Shell("pm list packages")
        for line in output.split("\n"):
            package_name = self.__clean_output(line)
            if package_name == "":
                continue

            if package_name not in self.packages:
                self.packages.append(Package(package_name))

        print(info("There are {} packages installed on the device.".format(len(self.packages))))
        print("")

    def pull_packages(self):
        print(info("Downloading packages from device. This might take some time ..."))
        print("")

        storage_folder_apk = os.path.join(self.storage_folder, "apks")
        if not os.path.exists(storage_folder_apk):
            os.mkdir(storage_folder_apk)

        total_packages = len(self.packages)
        counter = 0
        for package in self.packages:
            counter += 1

            print("[{}/{}] Package: {}".format(counter, total_packages, highlight(package.name)))

            try:
                output = self.device.Shell("pm path {}".format(package.name))
                output = self.__clean_output(output)
                if not output:
                    continue
            except Exception as e:
                print("ERROR: Failed to get path of package {}: {}".format(package.name, e))
                self.reconnect()
                continue

            # Sometimes the package path contains multiple lines for multiple apks.
            # We loop through each line and download each file.
            for path in output.split("\n"):
                path = path.strip()
                print("Downloading {} ...".format(path))

                try:
                    with PullProgress(unit='B', unit_divisor=1024, unit_scale=True, miniters=1) as pp:
                        data = self.device.Pull(path, progress_callback=pp.update_to)
                except Exception as e:
                    print("ERROR: Failed to pull package file from {}: {}".format(path, e))
                    self.reconnect()
                    continue

                # We try to extract the apk name for this package.
                file_name = ""
                if "==/" in path:
                    file_name = "_" + path.split("==/")[1].replace(".apk", "")

                # We store the apk to disk.
                file_path = os.path.join(storage_folder_apk, "{}{}.apk".format(package.name, file_name))
                with open(file_path, "wb") as handle:
                    handle.write(data)

                # We add the apk metadata to the package object.
                package.files.append({
                    "path": path,
                    "stored_path": file_path,
                    "sha256": get_sha256(file_path),
                })

            print("")

    def run(self):
        self.connect()

        self.get_packages()
        self.pull_packages()

        self.disconnect()
