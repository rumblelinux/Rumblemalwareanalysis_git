# Noriben Sandbox Automation Script
# Brian Baskin
# Part of the Noriben analysis repo
# Source: github.com/rurik/Noriben
#
# Changelog:
# V 2.0   - August 2023
#       Placed all editable data into Noriben.config file.
#       Cleaned up some logic and bugs
#       Added basic support for VirtualBox... still TODO
#       Rewrote post execution script parsing
#       A lot of random code cleanup
#
# V 1.3   - 15 Apr 19
#       Added ability to suspend or shutdown guest afterward.
#       Minor bug fixes.
# V 1.2.1 - 12 Sep 18
#       Bug fix to allow for snapshots with spaces in them.
# V 1.2   - 14 Jun 18
# V 1.1.1 - 8 Jan 18
# V 1.1   - 5 Jun 17
# V 1.0   - 3 Apr 17
#
# Responsible for:
# * Copying sample into a known VM
# * Running sample
# * Copying off results
#
# Ensure you set the environment variables in Noriben.conf to match your system.
# I've left defaults to help.

import os
import shlex
import subprocess
import sys
import time

import argparse
import codecs
import configparser
import io
import glob
import magic  # pip python-magic and libmagic


noriben_errors = {
    1: 'PML file was not found',
    2: 'Unable to find procmon.exe',
    3: 'Unable to create output directory',
    4: 'Windows is refusing execution based upon permissions',
    5: 'Could not create CSV',
    6: 'Could not find malware file',
    7: 'Error creating CSV',
    8: 'Error creating PML',
    9: 'Unknown error',
    10: 'Invalid arguments given',
    11: 'Missing Python module',
    12: 'Error in host module configuration',
    13: 'Required file not found',
    14: 'Configuration issue',
    50: 'General error'
}

error_count = 0
vm_hypervisor = ''
config = {}
debug = False
dontrun = False



def get_error(code):
    """
    Looks up a given code in dictionary set of errors of noriben_errors.

    Arguments:
        code: Integer that corresponds to a pre-set entry
    Results:
         string value of a error code
    """
    if code in noriben_errors:
        return noriben_errors[code]
    return 'Unexpected Error'


def file_exists(path):
    """
    Determine if a file exists

    Arguments:
        path: path to a file
    Results:
        boolean value if file exists
    """
    return os.path.exists(path) and os.access(path, os.F_OK) and not os.path.isdir(path)


def dir_exists(path):
    """
    Determine if a directory exists

    Arguments:
        path: path to a directory
    Results:
        boolean value if directory exists
    """
    return os.path.exists(path) and os.path.isdir(path)


def execute(cmd):
    """
    Executes a given command line on the host

    Arguments:
        cmd: String of command line to execute
    Result:
        none
    """
    if debug:
        print('[*] Executing: {}'.format(cmd))
    time.sleep(2)  # Extra sleep buffer as vmrun sometimes trips over itself
    stdout = subprocess.Popen(cmd, shell=True)
    stdout.wait()
    return stdout.returncode


def read_config(config_filename):
    """
    Parse an external configuration file.

    Arguments:
        config_filename: String of filename, predetermined if exists
    Result:
        none
    """
    global config

    try:
        file_config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
        with codecs.open(config_filename, 'r', encoding='utf-8') as f:
            file_config.read_file(f)

        config = {}
        options = file_config.options('Noriben_host')
        for option in options:
            config[option] = file_config.get('Noriben_host', option)

            if config[option].lower() in ['true', 'false']:
                config[option] = file_config.getboolean('Noriben_host', option)
            elif config[option] == -1:
                print('[*] Invalid configuration option detected: {}'.format(option))
    except configparser.MissingSectionHeaderError:
        print('[!] Error found in reading config file. Invalid section header detected.')
        sys.exit(12)
    except:
        print('[!] Exception occurred while reading config file on option %s!' % option)
        config[option] = None


def run_file(args, magic_result, malware_file):
    """
    Performs the actual execution of a file within the VM.
    This sets up the environment correctly and executes the file.

    Arguments:
        args: Object from argparse library containing all cmdline options
        magic_result: String containing Magic value of file
        malware_file: String path to file to execute
    Results:
         none
    """
    global error_count

    # Do this check once, so that all future comparisons can be simplified
    if vm_hypervisor not in ['vmw', 'vbox']:
        print('[!] Error! Unknown VM Hypervisor is set. Currently set to: {}'.format(vm_hypervisor))
        sys.exit(50)

    # First, normalize the configuration paths:
    guest_noriben_path = os.path.expanduser(config['guest_noriben_path'].format(config['vm_user']))

    host_malware_name_base = os.path.split(malware_file)[-1].split('.')[0]
    if dontrun:
        filename = '{}{}'.format(config['guest_malware_path'], host_malware_name_base)
        print('[*] File to be staged for analysis: {}'.format(filename))
    elif 'DOS batch' in magic_result:
        filename = '{}{}.bat'.format(config['guest_malware_path'], host_malware_name_base)
    else:
        filename = '{}{}.exe'.format(config['guest_malware_path'], host_malware_name_base)
    host_malware_path = os.path.dirname(malware_file)
    if host_malware_path == '':
        host_malware_path = '.'

    # These refer to the guest Windows-based paths
    # As the host OS is unknown, we'll build them manually instead of trying all the various methods
    guest_noriben_path_script = '{}\\{}'.format(guest_noriben_path, 'Noriben.py')
    guest_noriben_path_config = '{}\\{}'.format(guest_noriben_path, 'Noriben.config')
    guest_noriben_path_vt = '{}\\{}'.format(guest_noriben_path, 'virustotal.api')


    print('[*] Processing sample: {}'.format(malware_file))

    # First restore the VM to the specified snapshot
    # This is optional, especially for when one has prepped the VM manually for this specific execution
    if not args.norevert and not config['vm_snapshot'] == 'NO_SNAPSHOT_SPECIFIED':
        if vm_hypervisor == 'vmw':
            cmd = '"{}" -T ws revertToSnapshot {} "{}"'.format(config['vmrun'],
                                                               os.path.expanduser(config['vmx']),
                                                               os.path.expanduser(config['vm_snapshot']))
            return_code = execute(cmd)
            if return_code:
                print('[!] Error: Possible unknown snapshot or corrupt VMX: {}'.format(os.path.expanduser(config['vm_snapshot'])))
                sys.exit(return_code)

        elif vm_hypervisor == 'vbox':
            # VirtualBox requires powering off before restoring a snapshot.
            cmd = '"{}" controlvm {} poweroff'.format(config['vboxmanage'], config['vbox_uuid'])
            return_code = execute(cmd)
            if return_code == 1:
                # This is a normal return code. It's trying to poweroff a VM that is not running
                pass
            elif return_code:
                print('[!] Error code "{}": Unable to poweroff VM {{{}}}'.format(return_code, config['vbox_uuid']))
                sys.exit(return_code)

            cmd = '"{}" snapshot {} restore "{}"'.format(config['vboxmanage'], config['vbox_uuid'], os.path.expanduser(config['vm_snapshot']))
            return_code = execute(cmd)
            if return_code:
                print('[!] Error: Possible unknown snapshot or corrupt VMX: {}'.format(os.path.expanduser(config['vm_snapshot'])))
                sys.exit(return_code)

    # Power on the VM
    if vm_hypervisor == 'vmw':
        cmd = '"{}" -T ws start {}'.format(config['vmrun'], os.path.expanduser(config['vmx']))
    elif vm_hypervisor == 'vbox':
        cmd = '"{}" startvm {}'.format(config['vboxmanage'], config['vbox_uuid'])

    return_code = execute(cmd)
    if return_code:
        if return_code == 1 and vm_hypervisor == 'vbox':
            # This is the standard response for a VBox VM that is already running
            # Ignore it and move on to the next step
            print('[*] The above VBoxManage error is from trying to start a VM that is already running. This will be ignored.')
        else:
            print('[!] Error trying to start VM. Error {}: {}'.format(hex(return_code), get_error(return_code)))
            error_count += 1
            return return_code

    # Copy malware sample from host into VM
    if vm_hypervisor == 'vmw':
        cmd = '"{}" -gu {} -gp {} copyFileFromHostToGuest {} "{}" "{}"'.format(config['vmrun'], config['vm_user'],
                                                                               config['vm_pass'], os.path.expanduser(config['vmx']),
                                                                               malware_file, filename)
    elif vm_hypervisor == 'vbox':
        cmd = '"{}" guestcontrol {} copyto --username {} --password {} "{}" "{}"'.format(config['vboxmanage'], config['vbox_uuid'],
                                                                                         config['vm_user'], config['vm_pass'],
                                                                                         malware_file, filename)

    return_code = execute(cmd)
    if return_code:
        print('[!] Error trying to copy file to guest. Error {}: {}'.format(hex(return_code), get_error(return_code)))
        error_count += 1
        return return_code


    # --update - Copy the newest Noriben.py from the host into the guest VM. This saves one from having to make new snapshots
    #            just for new versions of the script. It also helps to run Noriben on VM that doesn't already have it.
    if args.update:
        if config['host_noriben_path']:
            host_noriben_path = config['host_noriben_path']
        else:
            host_noriben_path = sys.argv[0]
        host_noriben_path = os.path.dirname(os.path.abspath(host_noriben_path))

        host_noriben_path_script = os.path.join(host_noriben_path, 'Noriben.py')
        host_noriben_path_config = os.path.join(host_noriben_path, 'Noriben.config')
        host_noriben_path_vt     = os.path.join(host_noriben_path, 'virustotal.api')

        if file_exists(host_noriben_path_script):
            if vm_hypervisor == 'vmw':
                cmd = '"{}" -gu {} -gp {} copyFileFromHostToGuest {} "{}" "{}"'.format(config['vmrun'], config['vm_user'], config['vm_pass'], config['vmx'],
                                                                                       host_noriben_path_script.format(config['vm_user']),
                                                                                       guest_noriben_path_script)
            elif vm_hypervisor == 'vbox':
                cmd = '"{}" guestcontrol {} copyto --username {} --password {} "{}" "{}"'.format(config['vboxmanage'], config['vbox_uuid'],
                                                                                                 config['vm_user'], config['vm_pass'],
                                                                                                 host_noriben_path_script.format(config['vm_user']),
                                                                                                 guest_noriben_path_script)
            return_code = execute(cmd)
            if return_code:
                print('[!] Error trying to copy updated Noriben.py to guest. Continuing. Error {}: {}'.format(
                    hex(return_code), get_error(return_code)))
        else:
            print('[!] Noriben.py on host not found: {}'.format(host_noriben_path.format(config['vm_user'])))
            error_count += 1
            return return_code

        if file_exists(host_noriben_path_config):
            if vm_hypervisor == 'vmw':
                cmd = '"{}" -gu {} -gp {} copyFileFromHostToGuest {} "{}" "{}"'.format(config['vmrun'], config['vm_user'], config['vm_pass'], config['vmx'],
                                                                                       host_noriben_path_config.format(config['vm_user']),
                                                                                       guest_noriben_path_config)
            elif vm_hypervisor == 'vbox':
                cmd = '"{}" guestcontrol {} copyto --username {} --password {} "{}" "{}"'.format(config['vboxmanage'], config['vbox_uuid'],
                                                                                                 config['vm_user'], config['vm_pass'],
                                                                                                 host_noriben_path_config.format(config['vm_user']),
                                                                                                 guest_noriben_path_config)
            return_code = execute(cmd)
            if return_code:
                print('[!] Error trying to copy updated Noriben.config to guest. Continuing. Error {}: {}'.format(
                    hex(return_code), get_error(return_code)))
        else:
            # Not having Noriben.config will not error out. This can be expected in some situations.
            print('[!] Noriben.py on host not found: {}'.format(host_noriben_path.format(config['vm_user'])))

        if file_exists(host_noriben_path_vt):
            if vm_hypervisor == 'vmw':
                cmd = '"{}" -gu {} -gp {} copyFileFromHostToGuest {} "{}" "{}"'.format(config['vmrun'], config['vm_user'], config['vm_pass'], config['vmx'],
                                                                                       host_noriben_path_vt.format(config['vm_user']),
                                                                                       guest_noriben_path_vt)
            elif vm_hypervisor == 'vbox':
                cmd = '"{}" guestcontrol {} copyto --username {} --password {} "{}" "{}"'.format(config['vboxmanage'], config['vbox_uuid'],
                                                                                                 config['vm_user'], config['vm_pass'],
                                                                                                 host_noriben_path_vt.format(config['vm_user']),
                                                                                                 guest_noriben_path_vt)
            return_code = execute(cmd)
            if return_code:
                print('[!] Error trying to copy updated virustotal.api to guest. Continuing. Error {}: {}'.format(
                    hex(return_code), get_error(return_code)))
        else:
            # Not having virustotal.api will not error out. This is expected in most situations.
            # We won't even throw a message.
            pass

    # --dontrunanything is for cases where the analyst would like to spin up the VM, get files in place, and then stop.
    # They can then take control and do their own manual analysis.
    if args.dontrunanything:
        print('[*] --dontrunanything specified. Files have been copied successfully. Script will now terminate.')
        sys.exit(return_code)

    time.sleep(5)

    # --raw deletes the Procmon filter file prior to execution. There are likely better ways, but this
    # causes ProcMon to collect with default filters.
    if args.raw:
        procmon_config_path = config['procmon_config_path'].format(config['vm_user'])
        cmd = '"{}" -T ws -gu {} -gp {} deleteFileInGuest {} "{}"'.format(config['vmrun'], config['vm_user'], config['vm_pass'], os.path.expanduser(config['vmx']),
                                                                                      procmon_config_path)
        return_code = execute(cmd)

        # This errors if the Procmon filter cannot be found. We will allow this error.
        if return_code:
            print('[!] Error trying to delete Procmon filter "{}". Error {}: {}'.format(procmon_config_path,
                                                                                      hex(return_code),
                                                                                      get_error(return_code)))
            error_count += 1

    # Build the command line to run Noriben
    if vm_hypervisor == 'vmw':
        if not args.screenshot:
            active = '-activeWindow'
        else:
            active = ''

        cmd_base = '"{}" -T ws -gu {} -gp {} runProgramInGuest {} {} -interactive'.format(config['vmrun'], config['vm_user'], config['vm_pass'], os.path.expanduser(config['vmx']),
                                                                                          active)
        cmd = '{} "{}" "{}" -t {} --headless --output "{}" '.format(cmd_base, config['guest_python_path'],
                                                                    guest_noriben_path_script,
                                                                    config['timeout_seconds'], config['guest_log_path'])
    elif vm_hypervisor == 'vbox':
        cmd_base = '"{}" guestcontrol {} start --username {} --password {}'.format(config['vboxmanage'], config['vbox_uuid'], config['vm_user'], config['vm_pass'])
        print('cmd_base: {}'.format(cmd_base))

        cmd = '{} --exe "{}" -- "{}" -t {} --headless --output "{}" '.format(cmd_base, config['guest_python_path'],
                                                                    guest_noriben_path_script,
                                                                    config['timeout_seconds'], config['guest_log_path'])
    if not dontrun:
        cmd = '{} --cmd "{}" '.format(cmd, filename)

    if debug:
        cmd = '{} -d'.format(cmd)

    # Finally, execute the command line
    return_code = execute(cmd)

    if return_code:
        print('[!] Error in running Noriben. Error {}: {}'.format(hex(return_code), get_error(return_code)))
        error_count += 1
        return return_code


    if args.post and file_exists(args.post):
        run_postexec_script(args.post, cmd_base)

    zip_failed = False
    cmd = '{} "{}" -j "{}" "{}\\\\*.*"'.format(cmd_base, config['guest_zip_path'], config['guest_temp_zip'], config['guest_log_path'])
    return_code = execute(cmd)
    if return_code:
        print('[!] Error trying to zip report archive. Error {}: {}'.format(hex(return_code),
                                                                            get_error(return_code)))
        zip_failed = True

    host_report_path = config['report_path_structure'].format(host_malware_path, host_malware_name_base)
    if not args.nolog and not zip_failed:
        cmd = '"{}" -gu {} -gp {} copyFileFromGuestToHost {} "{}" "{}"'.format(config['vmrun'], config['vm_user'], config['vm_pass'], config['vmx'],
                                                                                 config['guest_temp_zip'], host_report_path)
        return_code = execute(cmd)
        if return_code:
            print('[!] Error trying to copy file from guest. Continuing. Error {}: {}'.format(hex(return_code),
                                                                                              get_error(return_code)))

    if args.screenshot:
        host_screenshot_path = config['host_screenshot_path_structure'].format(host_malware_path, host_malware_name_base)
        cmd = '"{}" -gu {} -gp {} captureScreen {} "{}"'.format(config['vmrun'], config['vm_user'], config['vm_pass'], config['vmx'],
                                                                  host_screenshot_path)
        return_code = execute(cmd)
        if return_code:
            print('[!] Error trying to create screenshot. Error {}: {}'.format(hex(return_code),
                                                                               get_error(return_code)))
        else:
            print('[*] Screenshot of desktop saved to: {}'.format(host_screenshot_path))

    if args.shutdown:
        cmd = '"{}" -T ws stop {}'.format(config['vmrun'], config['vmx'])
        return_code = execute(cmd)
        if return_code:
            print('[!] Error trying to start VM. Error {}: {}'.format(hex(return_code), get_error(return_code)))
            error_count += 1
            return return_code

    if args.suspend:
        cmd = '"{}" -T ws suspend {}'.format(config['vmrun'], config['vmx'])
        return_code = execute(cmd)
        if return_code:
            print('[!] Error trying to start VM. Error {}: {}'.format(hex(return_code), get_error(return_code)))
            error_count += 1
            return return_code

    # At this point, execution should be completed for the given file.
    print('[*] Execution completed for {}'.format(malware_file))
    if not args.nolog:
        print('[*] Logs stored at: {}'.format(host_report_path))
    return 0


def get_magic(magic_handle, filename):
    """
    Analyzes a given file to determine its magic value

    Arguments:
        magic_handle: Object from Magic library used to perform analysis
        filename: String path to file to analyze
    Results:
         none
    """
    try:
        magic_result = magic_handle.from_file(filename)
    except magic.MagicException as err:
        magic_result = ''
        if err.message == b'could not find any magic files!':
            print('[!] Windows Error: magic files not in path. See Dependencies on: ',
                  'https://github.com/ahupp/python-magic')
            print('[!] You may need to manually specify magic file location using --magic')
        print('[!] Error in running magic against file: {}'.format(err))

    if debug:
        print('[*] Magic result: {}'.format(magic_result))

    return magic_result


def run_postexec_script(postexec_script, cmd_base):
    """
    When the argument to run a post execution script is given, this function
    will open the specified file and process each line within it.

    Arguments:
        postexec_script: String path to post execution script
        cmd_base: String command line prefix for all execution
    Results:
         none
    """
    source_path = ''

    with io.open(postexec_script, encoding='utf-8') as post_script:
        for line in post_script:
            if debug:
                print('[*] Script: {}'.format(line.strip()))
            if len(line) <= 1:
                continue

            if line.startswith('#'):
                continue

            if line.lower().startswith('collect '):
                try:
                    source_path = line.split('collect ')[1].strip()
                except IndexError:
                    print('[!] Ignoring bad script collect: {}'.format(line.strip()))
                copy_file_to_zip(cmd_base, source_path)

            elif line.startswith('sleep '):
                try:
                    sleep_seconds = int(line.split('sleep ')[1].strip())
                except (IndexError, ValueError):
                    print('[!] Ignoring bad script sleep: {}'.format(line.strip()))
                    continue

                time.sleep(sleep_seconds)

            elif line.startswith('exec ') or line.startswith('execwait '):
                try:
                    items = shlex.split(line, posix=False)
                    cmd_type = items[0]
                    cmd_path = items[1]
                    cmd_args = ' '.join(items[2::])
                except IndexError:
                    print('[!] Ignoring bad script execution: {}'.format(line.strip()))
                    continue

                if cmd_type == 'exec':
                    cmd_nowait = '-noWait'
                else:
                    cmd_nowait = ''

                cmd = '{} {} "{}" "{}"'.format(cmd_base, cmd_nowait, cmd_path, cmd_args)
                return_code = execute(cmd)
                if return_code:
                    print('[!] Error trying to run script command. Error {}: {}'.format(hex(return_code),
                                                                                        get_error(return_code)))
            else:
                continue

def copy_file_to_zip(cmd_base, filename):
    """
    Adds a file contained with the VM to the exfiltration zip.
    This is a two-step process as zip.exe will not allow direct zipping
    of some system files. Therefore, first copy file to log folder and
    then add to the zip.

    Arguments:
        cmd_base: String command line prefix for all execution
        filename: String path of file within VM to extract
    Results:
         integer value of command line execution return code
    """
    global error_count

    cmd = '"{}" -gu {} -gp {} fileExistsInGuest {} "{}"'.format(config['vmrun'], config['vm_user'], config['vm_pass'],
                                                                  os.path.expanduser(config['vmx']), filename)
    return_code = execute(cmd)
    if return_code:
        print('[!] File does not exist in guest. Continuing. File: {}'.format(filename))
        error_count += 1
        return return_code

    cmd = '{} C:\\\\windows\\\\system32\\\\xcopy.exe "{}" "{}"'.format(cmd_base, filename, config['guest_log_path'])
    return_code = execute(cmd)
    if return_code:
        print(('[!] Error trying to copy file to log folder. Continuing. '
               'Error {}; File: {}'.format(return_code, filename)))
        error_count += 1
        return return_code

    cmd = '{} "{}" -j "{}" "{}"'.format(cmd_base, config['guest_zip_path'], config['guest_temp_zip'], filename)
    return_code = execute(cmd)

    if return_code:
        print(('[!] Error trying to add additional file to archive. Continuing. '
               'Error {}; File: {}'.format(return_code, filename)))
        error_count += 1
        return return_code
    return 0


def main():
    """
    Primary code. This parses command line arguments, sets configuration options,
    and starts the execution process

    Arguments:
        none
    Result:
        none
    """
    global config
    global error_count
    global debug
    global dontrun
    global script_cwd
    global vm_hypervisor

    # Error count is a soft trigger used for mass-execution to track when there was an abnormal
    # number of issues that execution should just stop
    error_count = 0

    script_cwd = os.path.dirname(os.path.abspath(__file__))

    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--file', help='filename', required=False)
    parser.add_argument('-d', '--debug', dest='debug', action='store_true', help='Show all commands for debugging',
                        required=False)
    parser.add_argument('-t', '--timeout', help='Number of seconds to collect activity', required=False, type=int)
    parser.add_argument('-x', '--dontrun', dest='dontrun', action='store_true', help='Execute Noriben, but not sample',
                        required=False)
    parser.add_argument('-xx', '--dontrunanything', dest='dontrunanything', action='store_true', help='Execute nothing',
                        required=False)
    parser.add_argument('--raw', action='store_true', help='Remove ProcMon filters', required=False)
    parser.add_argument('--update', action='store_true', help='Update Noriben.py in guest', required=False)

    parser.add_argument('--dir', help='Run all executables from a specified directory', required=False)
    parser.add_argument('--recursive', action='store_true', help='Recursively process a directory', required=False)
    parser.add_argument('--skip', action='store_true', help='Skip already executed files', required=False)
    parser.add_argument('--magic', help='Specify file magic database (may be necessary for Windows)', required=False)
    parser.add_argument('--config', help='Runtime configuration file', type=str, nargs='?', default='Noriben.config')

    parser.add_argument('--nolog', action='store_true', help='Do not extract logs back', required=False)
    parser.add_argument('--norevert', action='store_true', help='Do not revert to snapshot', required=False)
    parser.add_argument('--post', help='Post-execution script', required=False)
    parser.add_argument('--snapshot', help='Specify VM Snapshot to revert to', nargs='?', const='NO_SNAPSHOT_SPECIFIED', type=str)
    parser.add_argument('--vmx', help='Specify VM VMX file', required=False)
    parser.add_argument('--ignore', help='Ignore file or folder names that contain these comma-delimited terms', required=False)
    parser.add_argument('--shutdown', action='store_true', help='Powers down guest VM after execution', required=False)
    parser.add_argument('--suspend', action='store_true', help='Suspends guest VM after execution', required=False)
    parser.add_argument('--vbox', action='store_true', help='Use VirtualBox Hypervisor', required=False)
    parser.add_argument('--screenshot', action='store_true', help='Take screenshot after execution (PNG)',
                        required=False)

    args = parser.parse_args()

    # Load config file first, then use additional args to override those values if necessary
    if args.config:
        config_cwd = os.path.join(script_cwd, 'Noriben.config')

        if file_exists(args.config):  # Check arg path for current folder
            read_config(args.config)

        elif file_exists(config_cwd):
            read_config(config_cwd)
        else:
            print('[!] Config file {} not found!'.format(args.config))
            sys.exit(14)


    if not args.file and not args.dir:
        print(parser.print_help())
        print('[!] A filename or directory name are required!')
        sys.exit(13)

    if args.recursive and not args.dir:
        print('[!] Directory Recursive option specified, but not a directory')
        sys.exit(13)

    if not file_exists(config['vmrun']):
        print('[!] Path to vmrun does not exist: {}'.format(config['vmrun']))
        sys.exit(1)

    debug = bool(args.debug or config['debug'])
    dontrun = args.dontrun

    if args.vbox:
        vm_hypervisor = 'vbox'
    else:
        vm_hypervisor = 'vmw'

    if not config['vm_pass']:
        print('[!] vm_pass must be set in the configuration. VMware requires guest accounts to have passwords for remote access.')
        sys.exit(1)

    magic_handle = None
    try:
        if args.magic and file_exists(args.magic):
            magic_handle = magic.Magic(magic_file=args.magic)
        else:
            magic_handle = magic.Magic()
    except magic.MagicException as err:
        dontrun = True
        if err.message == b'could not find any magic files!':
            print('[!] Windows Error: magic files not in path. See Dependencies on: ',
                  'https://github.com/ahupp/python-magic')
            print('[!] You may need to manually specify magic file location using --magic')
        print('[!] Error in running magic against file: {}'.format(err))
        if args.dir:
            print('[!] Directory mode will not function without a magic database. Exiting')
            sys.exit(1)

    if args.snapshot:
        config['vm_snapshot'] = args.snapshot

    if args.vmx:
        if file_exists(os.path.expanduser(args.vmx)):
            config['vmx'] = os.path.expanduser(args.vmx)

    if args.timeout:
        config['timeout_seconds'] = args.timeout

    # Execute a specified file. This is typically the primary usage
    if args.file:
        if file_exists(args.file):
            magic_result = get_magic(magic_handle, args.file)

            # Make sure magic didn't fail, then let's determine if we want to skip it
            if magic_result and (not magic_result.startswith('PE32') or 'DLL' in magic_result):
                if 'DOS batch' not in magic_result:
                    dontrun = True
                    print('[*] Disabling automatic execution of sample due to magic signature: {}'.format(magic_result))
            run_file_response = run_file(args, magic_result, args.file)
        else:
            print('[!] Specified file cannot be found: {}'.format(args.file))
            sys.exit(13)

    # Enumerate all files within a specific directory for execution
    if args.dir:
        if dir_exists(args.dir):
            # Start the clock to measure total execution time
            # Helps to gather metrics for amount of time and resources required
            # If too short, it's a sign that detonation had issues. Exclude too much?
            exec_time = time.time()

            # Create a primary list of files to execute beforehand
            files = []
            for result in glob.iglob(args.dir):
                for (root, subdirs, filenames) in os.walk(result):
                    for fname in filenames:

                        # Parse out the specified ignore keywords for comparison against current file/folder
                        ignore = False
                        if args.ignore:
                            # Iterate through --ignore keywords and compare for exclusion
                            for ignore_item in args.ignore.split(','):
                                if (ignore_item.lower() in root.lower()) or (ignore_item.lower() in fname.lower()):
                                    ignore = True
                        if not ignore:
                            files.append(os.path.join(root, fname))

                    # If --recursive is not specified, then stop at the specified folder
                    if not args.recursive:
                        break

            # Parse through the list of accepted files
            for filename in files:
                if error_count >= int(config['error_tolerance']):
                    print('[!] Too many errors encountered in this run. Exiting.')
                    sys.exit(100)

                if args.skip and file_exists(filename + '_NoribenReport.zip'):
                    print('[!] Detonation already performed for file: {}'.format(filename))
                    continue

                # Front load magic processing to avoid unnecessary calls to run_file
                magic_result = get_magic(magic_handle, filename)
                if magic_result and magic_result.startswith('PE32') and 'DLL' not in magic_result:
                    if debug:
                        print('{}: {}'.format(filename, magic_result))
                    run_file_response = run_file(args, magic_result, filename)
                else:
                    print('[*] Directory parsing. File skipped as not an EXE or DLL: {} ({}...)'.format(filename, magic_result[0:50]))
                    continue


            exec_time_diff = time.time() - exec_time
            print('[*] Completed. Execution Time: {}'.format(exec_time_diff))
        else:
            print('[!] Specified directory cannot be found: {}'.format(args.dir))


if __name__ == '__main__':
    main()
