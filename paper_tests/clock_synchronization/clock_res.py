import time, datetime
import win_precise_time as wpt
import numpy as np

'''
Measures resolution of various clocks. Note that minimum is not necessarily correct information, as median shows
Also note that for some clocks the resolution you get may vary from run to run, at least on Windows,
depending on the current timer resolution.
'''

try:
    from time import timeout_time
except ImportError:
    from time import time as timeout_time

def compute_resolution(func):
    timeout = timeout_time() + 1.0
    previous = func()
    dts = []
    while timeout_time() < timeout or len(dts) < 3:
        for _ in range(10):
            t1 = func()
            t2 = func()
            dt = t2 - t1
            if dt > 0:
                break
        else:
            dt = t2 - previous
            if dt <= 0.0:
                continue

        dts.append(dt)
        previous = func()
    dts = np.asarray(dts)
    return np.min(dts), np.median(dts)

def format_duration(dt):
    if dt >= 1e-3:
        return "%.0f ms" % (dt * 1e3)
    if dt >= 1e-6:
        return "%.0f us" % (dt * 1e6)
    else:
        return "%.0f ns" % (dt * 1e9)

def test_clock(name, func):
    print("%s:" % name)
    resolution = compute_resolution(func)
    print("- determined resolution: min: %s, median: %s" % (format_duration(resolution[0]),format_duration(resolution[1])))


t0 = datetime.datetime.now()
dt_fun = lambda: (datetime.datetime.now()-t0).total_seconds()
clocks = [time.perf_counter, time.process_time, time.monotonic, time.time, wpt.time, dt_fun]
names  = ['perf_counter', 'process_time', 'monotonic', 'time', 'wpt.time', 'datetime.datetime.now']
for name, func in zip(names,clocks):
    test_clock("%s()" % name, func)
    if not name.startswith('wpt') and not name.startswith('datetime'):
        info = time.get_clock_info(name)
        print("- implementation: %s" % info.implementation)
        print("- reported resolution: %s" % format_duration(info.resolution))

# print some timestamps so we can see granularity
print()
for _ in range(10):
    print(f"{time.time()=:.9f} - {wpt.time()=:.9f} - {dt_fun()=:.9f}")
wpt.sleep(0.0001)
print('-----')
for _ in range(10):
    print(f"{time.time()=:.9f} - {wpt.time()=:.9f} - {dt_fun()=:.9f}")