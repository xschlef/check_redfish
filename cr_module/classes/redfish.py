# -*- coding: utf-8 -*-
#  Copyright (c) 2020 Ricardo Bartels. All rights reserved.
#
#  check_redfish.py
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see file LICENSE.txt included in this
#  repository or visit: <https://opensource.org/licenses/MIT>.

import os
import tempfile
import pickle
import json
import pprint
import sys


from cr_module.common import grab
from cr_module.classes import plugin_status_types
from cr_module.classes.vendor import *

# import 3rd party modules
import redfish

# defaults
default_conn_max_retries = 3
default_conn_timeout = 7


# noinspection PyBroadException
class RedfishConnection:

    session_file_path = None
    session_was_restored = False
    connection = None
    username = None
    password = None
    __cached_data = dict()
    vendor = None
    vendor_dict_key = None
    vendor_data = None
    cli_args = None

    def __init__(self, cli_args=None):

        if cli_args is None:
            raise Exception("No args passed to RedfishConnection()")

        if cli_args.host is None:
            raise Exception("cli args host not set")

        self.cli_args = cli_args

        self.session_file_path = self.get_session_file_name()
        self.restore_session_from_file()

        self.init_connection()

    @staticmethod
    def exit_on_error(message, level="UNKNOWN"):

        print("[%s]: %s" % (level, message))
        exit(plugin_status_types.get(level))

    def get_credentials(self):

        """
            Order of credential reading from highest to lowest priority
            1. cli_args username and password
            2. credentials from auth file
            3. credentials from environment
        """

        env_username_var = "CHECK_REDFISH_USERNAME"
        env_password_var = "CHECK_REDFISH_PASSWORD"

        # 1. if credentials are set via arguments then use them and return
        if self.cli_args.username is not None and self.cli_args.password is not None:
            self.username = self.cli_args.username
            self.password = self.cli_args.password
            return

        # 2. a authentication file is defined, lets try to parse it
        if self.cli_args.authfile is not None:

            try:
                with open(self.cli_args.authfile) as authfile:
                    for line in authfile:
                        name, var = line.partition("=")[::2]
                        if name.strip() == "username":
                            self.username = var.strip()
                        if name.strip() == "password":
                            self.password = var.strip()

            except FileNotFoundError:
                self.exit_on_error("Provided authentication file not found: %s" % self.cli_args.authfile)
            except PermissionError:
                self.exit_on_error("Error opening authentication file: %s" % self.cli_args.authfile)
            except Exception as e:
                self.exit_on_error(
                    "Unknown exception while trying to open authentication file %s: %s" % self.cli_args.authfile,
                    str(e))

            if self.username is None or self.password is None:
                self.exit_on_error(
                    "Error parsing authentication file '%s'. Make sure username and password are set properly." %
                    self.cli_args.authfile)

            return

        # 3. try to read credentials from environment
        self.username = os.getenv(env_username_var)
        self.password = os.getenv(env_password_var)

        return

    def get_session_file_name(self):

        default_session_file_prefix = "check_redfish_"
        default_session_file_suffix = ".session"

        if self.cli_args.sessionfiledir:
            session_file_dir = self.cli_args.sessionfiledir
        else:
            session_file_dir = tempfile.gettempdir()

        # check if directory is a file
        if os.path.isfile(session_file_dir):
            self.exit_on_error("The session file destination (%s) seems to be file." % session_file_dir)

        # check if directory exists
        if not os.path.exists(session_file_dir):
            # try to create directory
            try:
                os.makedirs(session_file_dir, 0o700)
            except OSError:
                self.exit_on_error("Unable to create session file directory: %s." % session_file_dir)
            except Exception as e:
                self.exit_on_error("Unknown exception while creating session file directory %s: %s" % session_file_dir,
                                   str(e))

        # check if directory is writable
        if not os.access(session_file_dir, os.X_OK | os.W_OK):
            self.exit_on_error("Error writing to session file directory: %s" % session_file_dir)

        # get full path to session file
        if self.cli_args.sessionfile:
            sessionfilename = self.cli_args.sessionfile
        else:
            sessionfilename = default_session_file_prefix + self.cli_args.host

        sessionfilepath = os.path.normpath(session_file_dir) + os.sep + sessionfilename + default_session_file_suffix

        if os.path.exists(sessionfilepath) and not os.access(sessionfilepath, os.R_OK):
            self.exit_on_error("Got no permission to read existing session file: %s" % sessionfilepath)

        if os.path.exists(sessionfilepath) and not os.access(sessionfilepath, os.W_OK):
            self.exit_on_error("Got no permission to write to existing session file: %s" % sessionfilepath)

        return sessionfilepath

    def restore_session_from_file(self):

        if self.session_file_path is None:
            raise Exception("sessionfilepath not set.")

        try:
            with open(self.session_file_path, 'rb') as pickled_session:
                self.connection = pickle.load(pickled_session)
        except (FileNotFoundError, EOFError):
            pass
        except PermissionError as e:
            self.exit_on_error("Error opening session file: %s" % str(e))
        except Exception as e:
            self.exit_on_error(
                "Unknown exception while trying to open session file %s: %s" % (self.session_file_path, str(e)))

        # restore root attribute as RisObject
        # unfortunately we have to re implement the code from get_root_object function
        try:
            root_data = json.loads(self.connection.root_resp.text)
        except AttributeError:
            root_data = None
        except ValueError:
            raise

        if root_data is not None:
            self.connection.root = redfish.rest.v1.RisObject.parse(root_data)

        # set possible changed connection values
        if self.connection is not None:
            self.connection._max_retry = self.cli_args.retries
            self.connection._timeout = self.cli_args.timeout

        self.session_was_restored = True

        return

    def save_session_to_file(self):

        if self.session_file_path is None:
            raise Exception("sessionfilepath not set")

        if self.connection is None:
            raise Exception("session not initialized")

        # unset root attribute
        # root attribute is an RisObject which can't be pickled
        root_data = self.connection.root
        self.connection.root = None

        # fix for change in redfish 2.0.10
        # Socket objects can't be pickled. Remove socket object from pickle object and add it back later on
        connection_socket = self.connection._conn
        connection_socket_count = self.connection._conn_count
        self.connection._conn = None
        self.connection._conn_count = 0

        try:
            with open(self.session_file_path, 'wb') as pickled_session:
                pickle.dump(self.connection, pickled_session)
        except PermissionError as e:
            self.exit_on_error("Error opening session file to save session: %s" % str(e))
        except Exception as e:

            # log out from current connection
            self.connection.logout()

            # try to delete session file
            try:
                os.remove(self.session_file_path)
            except Exception:
                pass

            self.exit_on_error(
                "Unknown exception while trying to save session to file %s: %s" % (self.session_file_path, str(e)))

        # set root attribute again
        self.connection.root = root_data

        # restore connection object
        self.connection._conn = connection_socket
        self.connection._conn_count = connection_socket_count

        return

    def init_connection(self, reset=False):

        # reset connection
        if reset is True:
            self.connection = None

        # if we have a connection object then just return
        if self.connection is not None:
            return

        self.get_credentials()

        # initialize connection
        try:
            self.connection = redfish.redfish_client(base_url="https://%s" % self.cli_args.host,
                                                     max_retry=self.cli_args.retries, timeout=self.cli_args.timeout)
        except redfish.rest.v1.ServerDownOrUnreachableError:
            self.exit_on_error("Host '%s' down or unreachable." % self.cli_args.host, "CRITICAL")
        except redfish.rest.v1.RetriesExhaustedError:
            self.exit_on_error("Unable to connect to Host '%s', max retries exhausted." % self.cli_args.host,
                               "CRITICAL")
        except Exception as e:
            self.exit_on_error("Unable to connect to Host '%s': %s" % (self.cli_args.host, str(e)), "CRITICAL")

        if not self.connection:
            raise Exception("Unable to establish connection.")

        if self.username is not None or self.password is not None:
            try:
                self.connection.login(username=self.username, password=self.password, auth="session")
            except redfish.rest.v1.RetriesExhaustedError:
                self.exit_on_error("Unable to connect to Host '%s', max retries exhausted." % self.cli_args.host,
                                   "CRITICAL")
            except redfish.rest.v1.InvalidCredentialsError:
                self.exit_on_error("Username or password invalid.", "CRITICAL")
            except Exception as e:
                self.exit_on_error("Unable to connect to Host '%s': %s" % (self.cli_args.host, str(e)), "CRITICAL")

        if self.connection is not None:
            self.connection.system_properties = None
            self.save_session_to_file()

        return

    def _rf_get(self, redfish_path):

        try:
            return self.connection.get(redfish_path, None)
        except redfish.rest.v1.RetriesExhaustedError:
            self.exit_on_error("Unable to connect to Host '%s', max retries exhausted." % self.cli_args.host,
                               "CRITICAL")

    def get(self, redfish_path):

        if self.__cached_data.get(redfish_path) is None:

            redfish_response = self._rf_get(redfish_path)

            # session invalid
            if redfish_response.status == 401:
                self.get_credentials()
                if self.username is None or self.password is None:
                    self.exit_on_error(f"Username and Password needed to connect to this BMC")

            if redfish_response.status != 404 and redfish_response.status >= 400 and self.session_was_restored is True:
                # reset connection
                self.init_connection(reset=True)

                # query again
                redfish_response = self._rf_get(redfish_path)

            # test if response is valid json and can be decoded
            try:
                redfish_response_json_data = redfish_response.dict
            except Exception:
                redfish_response_json_data = dict({"Members": list()})

            if self.cli_args.verbose:
                pprint.pprint(redfish_response_json_data, stream=sys.stderr)

            if redfish_response_json_data.get("error"):
                error = redfish_response_json_data.get("error").get("@Message.ExtendedInfo")
                self.exit_on_error(
                    "got error '%s' for API path '%s'" % (error[0].get("MessageId"), error[0].get("MessageArgs")))

            self.__cached_data[redfish_path] = redfish_response_json_data

        return self.__cached_data.get(redfish_path)

    def _rf_post(self, redfish_path, body):

        try:
            return self.connection.post(redfish_path, body=body)
        except redfish.rest.v1.RetriesExhaustedError:
            self.exit_on_error("Unable to connect to Host '%s', max retries exhausted." % self.cli_args.host,
                               "CRITICAL")

    def get_view(self, redfish_path=None):

        if self.vendor_data is not None and \
                self.vendor_data.view_select is not None and \
                self.vendor_data.view_supported:

            if self.vendor_data.view_response:
                return self.vendor_data.view_response

            redfish_response = self._rf_post("/redfish/v1/Views/", self.vendor_data.view_select)

            # session invalid
            if redfish_response.status != 404 and redfish_response.status >= 400 and self.session_was_restored is True:
                # reset connection
                self.init_connection(reset=True)

                # query again
                redfish_response = self._rf_post("/redfish/v1/Views/", self.vendor_data.view_select)

            # test if response is valid json and can be decoded
            redfish_response_json_data = None
            try:
                redfish_response_json_data = redfish_response.dict
            except Exception:
                pass

            if redfish_response_json_data is not None:
                if self.cli_args.verbose:
                    pprint.pprint(redfish_response_json_data)

                if redfish_response_json_data.get("error"):
                    error = redfish_response_json_data.get("error").get("@Message.ExtendedInfo")
                    self.exit_on_error(
                        "get error '%s' for API path '%s'" % (error[0].get("MessageId"), error[0].get("MessageArgs")))

                self.vendor_data.view_response = redfish_response_json_data

                return self.vendor_data.view_response

        if redfish_path is not None:
            return self.get(redfish_path)

        return None

    def determine_vendor(self):

        vendor_string = ""

        if self.connection.root.get("Oem"):

            if len(self.connection.root.get("Oem")) > 0:
                vendor_string = list(self.connection.root.get("Oem"))[0]

            self.vendor_dict_key = vendor_string

            if vendor_string in ["Hpe", "Hp"]:

                self.vendor_data = VendorHPEData()

                manager_data = grab(self.connection.root, f"Oem.{vendor_string}.Manager.0")

                if manager_data is not None:
                    self.vendor_data.ilo_hostname = manager_data.get("HostName")
                    self.vendor_data.ilo_version = manager_data.get("ManagerType")
                    self.vendor_data.ilo_firmware_version = manager_data.get("ManagerFirmwareVersion")

                    if self.vendor_data.ilo_version.lower() == "ilo 5":
                        self.vendor_data.view_supported = True

            if vendor_string in ["Lenovo"]:

                self.vendor_data = VendorLenovoData()

            if vendor_string in ["Dell"]:

                self.vendor_data = VendorDellData()

            if vendor_string in ["Huawei"]:

                self.vendor_data = VendorHuaweiData()

            if vendor_string in ["ts_fujitsu"]:

                self.vendor_data = VendorFujitsuData()

        # Cisco does not provide a OEM property in root object
        if "CIMC" in str(self.get_system_properties("managers")):

            self.vendor_data = VendorCiscoData()
            self.vendor_dict_key = self.vendor_data.name

        if self.vendor_data is None:

            self.vendor_data = VendorGeneric()

            if vendor_string is not None and len(vendor_string) > 0:
                self.vendor_data.name = vendor_string

        self.vendor = self.vendor_data.name

        return

    def discover_system_properties(self):

        if vars(self.connection).get("system_properties") is not None:
            return

        system_properties = dict()

        root_objects = ["Chassis", "Managers", "Systems"]

        for root_object in root_objects:
            if self.connection.root.get(root_object) is None:
                continue

            rf_path = self.get(self.connection.root.get(root_object).get("@odata.id"))

            if rf_path is None:
                continue

            system_properties[root_object.lower()] = list()
            for entity in rf_path.get("Members"):

                # ToDo:
                #  * This is a DELL workaround
                #  * If RAID chassi is requested the iDRAC will restart
                if root_object == "Chassis" and \
                        ("RAID" in entity.get("@odata.id") or "Enclosure" in entity.get("@odata.id")):
                    continue

                system_properties[root_object.lower()].append(entity.get("@odata.id"))

        self.connection.system_properties = system_properties
        self.save_session_to_file()

        return

    def get_system_properties(self, requested_property=None):
        """
        get a list of links to system properties for requested_property
        i.e.:
            "systems" -> [ "/redfish/v1/Systems/1" ]

        if no property is requested the whole dict will be returned

        Parameters
        ----------
        requested_property: str
            can be either "chassis", "managers", "systems" or None

        Returns
        -------
            list, dict
                list of property links if requested_property was set or
                dict of all properties if no requested_property was set
        """

        if requested_property is not None and requested_property not in ["chassis", "managers", "systems"]:
            raise Exception(f"Invalid property '{requested_property}' requested.")

        if self.connection.system_properties is None:
            self.discover_system_properties()

        if self.connection.system_properties is not None:
            if requested_property is not None:
                return self.connection.system_properties.get(requested_property)
            else:
                return self.connection.system_properties

        return None
# EOF
