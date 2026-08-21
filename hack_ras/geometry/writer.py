# hack_ras/geometry/writer.py

from .model import GeometryFile

class GeometryWriter:
    def write(self, geom: GeometryFile, path: str):
        """
        Write the original raw lines back out.
        """
        # Explicit utf-8 to match GeometryParser, rather than inheriting the
        # machine's locale codepage. newline is deliberately NOT set: the
        # parser reads with universal newlines (CRLF -> LF in raw_lines) and
        # this write translates back, which is what makes the round trip
        # byte-identical on Windows.
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(geom.raw_lines)
