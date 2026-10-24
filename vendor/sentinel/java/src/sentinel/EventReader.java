package sentinel;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

/** Reads events.bin: 8-byte magic, int64 count, then 15 little-endian int64 fields per event. */
public final class EventReader {
    public static final int FIELDS = 15;

    /** All events in memory, row-major. */
    public static final class Events {
        public final long[] data;
        public final int n;

        Events(long[] data, int n) {
            this.data = data;
            this.n = n;
        }
    }

    private EventReader() {
    }

    public static Events read(Path path, int maxEvents) throws IOException {
        try (FileChannel ch = FileChannel.open(path, StandardOpenOption.READ)) {
            ByteBuffer header = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN);
            ch.read(header, 0);
            header.flip();
            byte[] magic = new byte[8];
            header.get(magic);
            if (!new String(magic, StandardCharsets.US_ASCII).equals("SNTLEV01")) {
                throw new IOException("not an events.bin file: " + path);
            }
            long total = header.getLong();
            int n = (int) Math.min(total, (long) maxEvents);
            long[] data = new long[n * FIELDS];
            MappedByteBuffer buffer = ch.map(FileChannel.MapMode.READ_ONLY, 16, (long) n * FIELDS * 8);
            buffer.order(ByteOrder.LITTLE_ENDIAN).asLongBuffer().get(data);
            return new Events(data, n);
        }
    }
}
