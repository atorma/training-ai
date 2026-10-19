"""Live notifications and replayable streams for this server's active runs."""

import asyncio
from tempfile import SpooledTemporaryFile
from weakref import finalize

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
HEARTBEAT = ": keep-alive\n\n"


class RunStream:
    def __init__(self):
        # Keep small streams in RAM; spill larger replies to a private temporary file.
        self.buffer = SpooledTemporaryFile(max_size=256 * 1024, mode="w+b")
        # Existing subscribers may still drain after removal from the active-run registry.
        # Close as soon as the run and all of its readers release their references.
        self._cleanup = finalize(self, self.buffer.close)
        self.size = 0
        self.finished = False
        self.changed = asyncio.Condition()

    async def publish(self, frame):
        data = frame.encode("utf-8")
        async with self.changed:
            self.buffer.seek(self.size)
            self.buffer.write(data)
            self.size += len(data)
            self.changed.notify_all()

    async def finish(self):
        async with self.changed:
            self.finished = True
            self.changed.notify_all()

    async def subscribe(self):
        """Replay from the beginning, then follow; readers never own generation."""
        offset = 0
        while True:
            async with self.changed:
                if offset == self.size and not self.finished:
                    try:
                        async with asyncio.timeout(15):
                            await self.changed.wait()
                    except TimeoutError:
                        pass
                self.buffer.seek(offset)
                data = self.buffer.read(32 * 1024)
                offset += len(data)
                finished = self.finished and offset == self.size
            if data:
                yield data
            elif not finished:
                yield HEARTBEAT
            if finished:
                return


class ConversationEvents:
    def __init__(self):
        self.runs = {}
        self.listeners = set()
        self.closed = False

    def notify(self):
        # One pending notification per browser is enough: each fetches a fresh snapshot.
        for queue in self.listeners:
            if queue.empty():
                queue.put_nowait(None)

    def close(self):
        self.closed = True
        self.notify()

    async def subscribe(self):
        queue = asyncio.Queue(maxsize=1)
        self.listeners.add(queue)
        try:
            # Register before the snapshot fetch to avoid missing concurrent mutations.
            yield "event: ready\ndata: {}\n\n"
            while not self.closed:
                try:
                    async with asyncio.timeout(15):
                        await queue.get()
                    if not self.closed:
                        yield "event: changed\ndata: {}\n\n"
                except TimeoutError:
                    yield HEARTBEAT
        finally:
            self.listeners.discard(queue)
