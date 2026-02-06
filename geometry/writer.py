# hack_ras/geometry/writer.py

from .model import GeometryFile

class GeometryWriter:
    def write(self, geom: GeometryFile, path: str):
        """
        Write the original raw lines back out.
        """
        with open(path, "w") as f:
            f.writelines(geom.raw_lines)
