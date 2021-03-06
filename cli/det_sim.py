import asyncio
import zmq
import zmq.asyncio
import numpy as np
from enum import Enum
from collections import defaultdict


class CMDS(Enum):
    REG_READ = 0
    REG_WRITE = 1
    START_DMA = 2


FIFODATAREG = 24
FIFORDCNTREG = 25
FIFOCNTRLREG = 26

FRAMEACTIVEREG = 52
FRAMENUMREG = 54
FRAMELENREG = 53


ctx = zmq.asyncio.Context()
loop = zmq.asyncio.ZMQEventLoop()
asyncio.set_event_loop(loop)

# average number of events per msg
N = 50000
# number of messages
n_msgs = 5
# average total exposure
simulated_exposure = 10
# expected ticks between events
# ([S] / [event]) * ([tick] / [s]) = [tick] / [event]
tick_gap = int((simulated_exposure / N) / (40 * 10e-9))


def simulate_line(n):
    return np.random.randint(2**12, size=n, dtype=np.uint64)


@asyncio.coroutine
def recv_and_process():
    responder = ctx.socket(zmq.REP)
    publisher = ctx.socket(zmq.PUB)
    responder.bind(b'tcp://*:5555')
    publisher.bind(b'tcp://*:5556')
    state = defaultdict(int)

    ts_offset = 0

    @asyncio.coroutine
    def sim_data():
        nonlocal ts_offset
        state[FRAMENUMREG] += 1
        for j in range(n_msgs):
            # simulate 4 active chips
            chip_id = np.random.randint(4, size=N, dtype=np.uint64) << (27+32)
            # simulate 4 active channels per chip
            chan_id = np.random.randint(4, size=N, dtype=np.uint64) << (22+32)
            # fine timestamp
            td = np.random.randint(2**10, size=N, dtype=np.uint64) << (12+32)
            # energy
            pd = simulate_line(N) << 32
            # coarse timestamp
            ts = np.mod((np.cumsum(np.random.poisson(tick_gap, size=N)) +
                         ts_offset),
                        2**31)
            ts_offset = ts[-1]
            payload = chip_id + chan_id + td + pd + ts_offset
            yield from publisher.send_multipart([b'data',
                                                 payload])

        yield from publisher.send_multipart([b'meta',
                                             np.uint32(state[FRAMENUMREG])])

    while True:
        msg = yield from responder.recv_multipart()
        for m in msg:
            cmd, addr, value = np.frombuffer(m, dtype=np.int32)
            cmd = CMDS(cmd)
            if cmd == CMDS.REG_WRITE:
                state[addr] = value
                yield from responder.send(m)
                if addr == 0 and value == 1:
                    yield from sim_data()
            elif cmd == CMDS.REG_READ:
                value = state[addr]
                reply = np.array([cmd.value, addr, value], dtype=np.uint32)
                yield from responder.send(reply)
            else:
                yield from responder.send(np.ones(3, dtype=np.uint32) * 0xdead)


loop.run_until_complete(recv_and_process())
