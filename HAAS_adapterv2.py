import threading
import time
import socket
import sys
import os
import datetime
import linuxcnc

client_counter = 0
client_list = []
first_run_flag = 1
lock = threading.Lock()
event = threading.Event()
event.set()

# Global attributes for adapter output
mac_status = part_num = prog_name = sspeed = coolant = sload = cut_status = combined_output = 'Nil'

# Socket config d
HOST = ''
PORT = 7878

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

try:
    s.bind((HOST, PORT))
except socket.error as msg:
    print('Bind failed. Error Code : ' + str(msg[0]) + ' Message ' + msg[1])
    sys.exit()

s.listen(5)


def thread_list_empty():
    """Clear out finished client threads."""
    global client_list, client_counter, first_run_flag

    while True:
        try:
            if client_counter == 0 and first_run_flag == 0 and client_list:
                print("%d Clients Active" % client_counter)
                print("Clearing all threads....")
                for thread in client_list:
                    thread.join()
                client_list = []
        except Exception as e:
            print("Invalid Client List Deletion:", e)
        time.sleep(1)


def fetch_from_HAAS():
    """
    Periodically poll LinuxCNC and build the MTConnect-style output string.

    Here we mainly care about "online" status:
    - mac_status = 'ON' if LinuxCNC task is ON / ESTOP_RESET and enabled, estop not active
    - mac_status = 'OFF' otherwise
    """
    global mac_status, part_num, prog_name, sspeed, coolant, sload, cut_status, combined_output

    try:
        stat = linuxcnc.stat()
    except linuxcnc.error as e:
        print("Failed to connect to LinuxCNC status channel:", e)
        event.clear()
        return

    # Try to get machine name from ini (optional)
    try:
        stat.poll()
        inifile = linuxcnc.ini(stat.ini_filename)
        machine_name = inifile.find("EMC", "MACHINE") or "unknown"
        print("Machine name:", machine_name)
    except Exception as e:
        print("Could not read INI / machine name:", e)

    while event.is_set():
        out = ''

        try:
            stat.poll()
        except linuxcnc.error as e:
            # If poll fails, assume machine is offline
            mac_status = 'OFF'
            combined_output = '\r\n' + datetime.datetime.now().isoformat() + 'Z' + '|power|OFF'
            print("LinuxCNC status poll failed:", e)
            time.sleep(1.0)
            continue

        # --- ONLINE / POWER STATUS ---
        try:
            # Conservative definition of "ON"
            if (stat.task_state in (linuxcnc.STATE_ON, linuxcnc.STATE_ESTOP_RESET)
                    and not stat.estop
                    and stat.enabled):
                mac_status = 'ON'
            else:
                mac_status = 'OFF'
        except AttributeError:
            # Fallback if some attributes missing
            mac_status = 'OFF'

        out += '|power|' + str(mac_status)
        
        # --- ACTIVE TOOL INFO (number, length, diameter) ---
        try:
                tool_no = int(getattr(stat, 'tool_in_spindle', 0) or 0)    
                tool_len = 'UNAVAILABLE'
                tool_dia = 'UNAVAILABLE'
        except (TypeError, ValueError):
                tool_no = 0
                
        tool_table = getattr(stat, 'tool_table', None)
        if tool_table and tool_no:
                for entry in tool_table:
                # Many LinuxCNC builds expose tool_result objects,
                # but some give plain tuples – handle both.
                        try:
                                tnum = getattr(entry, 'toolno', getattr(entry, 'id', None))
                                if tnum != tool_no:
                                        continue
                                tool_len = getattr(entry, 'zoffset', tool_len)
                                tool_dia = getattr(entry, 'diameter', tool_dia)
                                break
                        except AttributeError:
                        # Fallback: standard tuple layout:
                        # (toolno, pocket, xoff, zoff, aoff, boff, coff, uoff, voff, woff, diameter, frontangle, backangle, orientation)
                                if len(entry) > 10 and entry[0] == tool_no:
                                        tool_len = entry[3]   # zoffset
                                        tool_dia = entry[10]  # diameter
                                        break
                        
        # --- PROGRAM / PART COUNT (simple placeholders) ---
        try:
            prog_full = getattr(stat, "file", "")
            prog_name = os.path.basename(prog_full) if prog_full else "Nil"
        except AttributeError:
            prog_name = 'Nil'

        # You can hook this to an actual counter if you want
        part_num = 0

        out += '|PartCountAct|' + str(part_num)
        out += '|program|' + str(prog_name)

        # --- SPINDLE SPEED (optional; ignore errors) ---
        # --- SPINDLE SPEED (actual RPM) ---
        try:
                if hasattr(stat, "spindle"):
                # Newer LinuxCNC: spindle dict
                        sspeed = "{:.4f}".format(stat.spindle[0]['speed'])   # float RPM, >0 CW, <0 CCW
                else:
                # Older LinuxCNC: legacy attribute, if present
                        sspeed = "{:.4f}".format(getattr(stat, "spindle_speed", 0.0))
                if sspeed is None:
                        sspeed = 0.0
        except Exception as e:
                print("Error reading spindle speed:", e)
                sspeed = 0.0

        # --- COOLANT / LOAD / EXECUTION (stubbed for now) ---
        coolant = 'Nil'
        sload = 'Nil'
        cut_status = 'Nil'
        out += '|Sload|' + sload
        out += '|execution|' + cut_status

        # --- POSITION (optional, stubbed) ---
        pos = getattr(stat, "actual_position", None)
        if pos and len(pos) >= 3:
                x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
                # Format like Haas: space-separated vector
                #xyzact = "{:.4f} {:.4f}; {:.4f}".format(x, y, z)
                xact = "{:.4f}".format(x)
                yact = "{:.4f}".format(y)
                zact = "{:.4f}".format(z)
        else:
                #xyzact = ''
                xact = ''
                yact = ''
                zact = ''
        #out = f'|xyzact|{xyzact}'
        out = f'|Xact|{xact}'
        out += f'|Yact|{yact}'
        out += f'|Zact|{zact}'
        out += f'|tool_no|{tool_no}'
        out += f'|tool_length|{tool_len}'
        out += f'|tool_radius|{tool_dia}'
        out += f'|Srpm|{sspeed}'
	          

        # Final combined output line
        combined_output = '\r\n' + datetime.datetime.now().isoformat() + 'Z' + out

        time.sleep(0.2)


class NewClientThread(threading.Thread):
    def __init__(self, conn, string_address):
        threading.Thread.__init__(self)
        self.connection_object = conn
        self.client_ip = string_address

    def run(self):
        global client_counter, combined_output, lock
        while True:
            try:
                print(f"Sending data to Client {self.client_ip} in {self.getName()}")
                out = combined_output
                # Ensure bytes for Python 3
                self.connection_object.sendall(out.encode('ascii', errors='ignore'))
                time.sleep(0.5)

            except Exception as e:
                print("Client send failed:", e)
                lock.acquire()
                try:
                    client_counter -= 1
                    print("Connection disconnected for ip {} ".format(self.client_ip))
                    break
                finally:
                    lock.release()


# --- Main startup ---
t1 = threading.Thread(target=thread_list_empty, daemon=True)
t2 = threading.Thread(target=fetch_from_HAAS, daemon=True)
t1.start()
t2.start()
time.sleep(2)

while event.is_set():
    if first_run_flag == 1:
        print("Listening to Port: %d...." % PORT)

    try:
        conn, addr = s.accept()
        lock.acquire()
        client_counter += 1
        first_run_flag = 0
        print("Accepting Comm From:", addr)
        new_client_thread = NewClientThread(conn, str(addr))
        new_client_thread.setDaemon(True)
        client_list.append(new_client_thread)
        new_client_thread.start()
        lock.release()
    except KeyboardInterrupt:
        print("\nExiting Program")
        event.clear()
        break
    except Exception as e:
        print("Error accepting connection:", e)

print("\nExiting Program")
sys.exit()

