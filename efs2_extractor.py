from argparse import ArgumentParser
from construct import *
import hexdump
import os
import re
import struct
import sys
from tqdm import tqdm
import zipfile

# NAND layout
# TODO: Bad block handling + ECC correction
nand_device = Array(1024,
"nand_block" / Array(64,
    "nand_page" / Struct(
        "p11" / Bytes(0x1d0),
        Padding(1),
        "p12" / Bytes(0x34),
        Padding(0xb),
        "p21" / Bytes(0x1d0),
        Padding(1),
        "p22" / Bytes(0x34),
        Padding(0xb),
        "p31" / Bytes(0x1d0),
        Padding(1),
        "p32" / Bytes(0x34),
        Padding(0xb),
        "p41" / Bytes(0x1d0),
        Padding(1),
        "p42" / Bytes(0x34-16),
        Padding(0xb),
        Padding(16),
        "data" / Computed(this.p11+this.p12+this.p21 +
                        this.p22+this.p31+this.p32+this.p41+this.p42),
        ),
    ),
)

# NAND partition table
nand_partition_table = Struct(
    "magic1" / Const(b'\xaa\x73\xee\x55'),
    "magic2" / Const(b'\xdb\xbd\x5e\xe3'),
    "p_ver" / Int32ul,
    "p_nbr" / Int32ul,
    "parts" / Array(this.p_nbr,
        Struct(
            "flash" / Bytes(1),
            Padding(1),
            "name" / PaddedString(14, "utf8"),
            "block_start" / Hex(Int32ul),
            "block_length" / Hex(Int32ul),
            "attr" / Bytes(4),
        ),
    ),
)

def extract_nand_partitions(f, output_dir) -> list:
    try:
        nand_blocks = nand_device.parse_file(f)
    except StreamError:
        sys.exit("[E] Incorrect NAND parsing (layout?)")

    for blocks in nand_blocks:
        for block in blocks:
            try:
                p = nand_partition_table.parse(block["data"])
                print("[I] Partition table found ! Extracting...")
                break
            except ConstError:
                continue
    
    if p is None:
        sys.exit('[E] No partition table found')

    for part in p.parts:
        output_file = open(os.path.join(output_dir, part["name"]), "wb")
        for block in tqdm(range(part["block_start"], part["block_start"]+part["block_length"]), desc=f"[I] Processing {part['name']}"):
            for page in nand_blocks[block]:
                output_file.write(page.data)
        output_file.flush()
        output_file.close()
    print(f"[I] Partitions extracted successfully !")
    return p.parts


# QEFS2 EFS info block
efs_info_data = Struct(
    "magic" / Const(b"\xa0\x3e\xb9\xa7"),
    "version" / Hex(Int32ul),
    "inode_top" / Hex(Int32ul),
    "inode_next" / Hex(Int32ul),
    "inode_free" / Hex(Int32ul),
    "root_inode" / Hex(Int32ul),
    "partial_delete" / Hex(Int8ul),
    "partial_delete_mid" / Hex(Int8ul),
    "partial_delete_gid" / Hex(Int16ul),
    "partial_delete_data" / Array(4, Hex(Int32ul)),
)

# QEFS2 Node
node_data = Struct(
    "prev" / Hex(Int32ul),
    "next" / Hex(Int32ul),
    "used" / Hex(Int16ul),
    "pad" / Hex(Int16ul),
    "gid" / Hex(Int32ul),
    "bogus_count" / Hex(Byte),
    "level" / Hex(Byte),
    "data" / Bytes(this.used),
)

# QEFS2 SuperBlock
superblock_data = Struct(
    "page_header" / Hex(Int32ul),
    "version" / Hex(Int16ul),
    "age" / Hex(Int16ul),
    "magic1" / Const(b'\x45\x46\x53\x53'),
    "magic2" / Const(b'\x75\x70\x65\x72'),
    "block_size" / Hex(Int32ul),
    "page_size" / Hex(Int32ul),
    "block_count" / Hex(Int32ul),
    "block_length" / Hex(Computed(this.block_size * \
                                  this.page_size)),
    "log_head" / Hex(Int32ul),
    "alloc_next" / Array(4, Hex(Int32ul)),
    "gc_next" / Array(4, Hex(Int32ul)),
    "upper_data" / Array(32, Hex(Int32ul)),
    "nand_info" / Struct(
        "nodes_per_page" / Hex(Int16ul),
        "page_depth" / Hex(Int16ul),
        "super_nodes" / Hex(Int16ul),
        "num_regions" / Hex(Int16ul),
        "regions" / Array(this.num_regions, Hex(Int32ul)),
        "logr_badmap" / Hex(Int32ul),
        "pad" / Hex(Int32ul),
        "tables" / Hex(Int32ul),
    ),
    "pt" / Pointer(this.nand_info.tables*this.page_size, Array(512, Int32ul)),
    "efs_info" / Pointer(this.pt[3]*this.page_size, efs_info_data),
)

# QEFS2 inode
fs_inode_data = Struct(
    "mode" / Hex(Int16ul),
    "nlink" / Hex(Int16ul),
    "attr" / Hex(Int32ul),
    "size" / Hex(Int32ul),
    "uid" / Hex(Int16ul),
    "gid" / Hex(Int16ul),
    "generation" / Hex(Int32ul),
    "blocks" / Hex(Int32ul),
    "mtime" / Hex(Int32ul),
    "ctime" / Hex(Int32ul),
    "atime" / Hex(Int32ul),
    "reserved" / Array(7, Hex(Int32ul)),
    "direct_cluster_id" / Array(13, Hex(Int32ul)),
    "indirect_cluster_id" / Array(3, Hex(Int32ul)),
)


class FileEntry:
    def __init__(self):
        self.name = None
        self.parent_inode = None
        self.inode = None
        self.mode = None
        self.data = None

    def is_dir(self):
        return (self.mode & 0xf000) == 0x4000

    def is_file(self):
        return (self.mode & 0xf000) == 0x8000


class FileDescriptor:
    # TODO: additional fields
    def __init__(self):
        self.mode = None
        self.data = bytes()



def parse_file_list(f, sb, node_id):
    page_id = sb["pt"][node_id]
    global_offset = page_id * sb["page_size"]
    f.seek(global_offset)
    node = node_data.parse_stream(f)
    data = node["data"]

    entries: list[FileEntry] = []
    off = 0
    while off < len(data):
        flen, mlen = data[off], data[off+1]
        off += 2

        if flen >= 103:
            raise ValueError('LONG NAME ENCOUNTERED')

        fdata = data[off:off+flen]
        off += flen

        dd = chr(fdata[0])
        dd_inode, = struct.unpack_from('<I', fdata, 1)

        if dd != 'd':
            raise ValueError(f'unexpected d value: {dd}')

        filename = fdata[5:]
        if len(filename) == 0:
            filename = '.'
        elif len(filename) == 1 and filename[0] == 0x00:
            filename = '..'
        else:
            filename = filename.decode()

        mdata = data[off:off+mlen]
        off += mlen

        m_dd = chr(mdata[0])
        if m_dd == 'i':
            m_inode, = struct.unpack_from('<I', mdata, 1)

            fd = fetch_file_descriptor(f, sb, m_inode)

            entry = FileEntry()
            entry.name = filename
            entry.parent_inode = dd_inode
            entry.inode = m_inode
            entry.mode = fd.mode
            entry.data = fd.data
            entries.append(entry)

        elif m_dd == 'n':
            # little n: mode, filedata, No data descriptor association (no inode)
            m_mode,  = struct.unpack_from('<H', mdata, 1)
            m_data = mdata[3:]

            entry = FileEntry()
            entry.name = filename
            entry.parent_inode = dd_inode
            entry.inode = None
            entry.mode = m_mode
            entry.data = m_data
            entries.append(entry)

        elif m_dd == 'N':
            # mode, gid, ctime, filedata. No data descriptor association (no inode)
            m_mode, gid, ctime = struct.unpack_from('<HHI', mdata, 1)
            m_data = mdata[9:]

            entry = FileEntry()
            entry.name = filename
            entry.parent_inode = dd_inode
            entry.inode = None
            entry.mode = m_mode
            entry.data = m_data
            entries.append(entry)

        else:
            print(
                f' - {dd} inode 0x{dd_inode:08x} {filename} --> {m_dd} {mdata[1:].hex()}')
            raise ValueError('not implemented')

    if off > len(data):
        raise ValueError('overrun')

    return node.prev, node.next, entries


def fetch_file_descriptor(f, sb, inode: int):
    # TODO: Put canonical address extraction
    cluster_id = inode >> 4
    index = inode & 0xf

    page_id = sb["pt"][cluster_id]
    global_offset = page_id * sb["page_size"]
    f.seek(global_offset+0x80*index)

    fi = fs_inode_data.parse(f.read(0x80))

    fd = FileDescriptor()
    fd.mode = fi.mode

    nb = 0
    # Direct pages
    for page_id in fi.direct_cluster_id:
        if page_id == 0xffffffff:
            break
        if nb >= fi.blocks:
            break
        f.seek(sb["pt"][page_id]*sb["page_size"])
        fd.data += f.read(sb["page_size"])
        nb += 1

    # Indirect pages
    for page_id in fi.indirect_cluster_id:
        if page_id == 0xffffffff:
            break
        f.seek(sb["pt"][page_id]*sb["page_size"])
        page = f.read(sb["page_size"])
        for ipage_id, in struct.iter_unpack('<I', page):
            if ipage_id == 0xffffffff:
                break
            if nb >= fi.blocks:
                break
            f.seek(sb["pt"][ipage_id]*sb["page_size"])
            fd.data += f.read(sb["page_size"])
            nb += 1

    # Truncate length
    fd.data = fd.data[18:fi.size]
    return fd


def extract_efs2(input_file, output_dir):
    print(f"[I] Extracting files from : {input_file}")
    root_zip_dir = input_file.split("/")[-1:][0]
    with open(input_file, "rb") as f:
        i = 0
        age = 0
        # Find most recent superblock
        # TODO: check age rollover
        while True:
            try:
                s = superblock_data.parse_stream(f)
                if s["age"] > age:
                    age = s["age"]
                    sb = s
            except ConstError:
                i = i+1
                f.seek(i*0x800)
            except StreamError:
                break

        files = []
        root_inode = sb["efs_info"]["root_inode"]
        next_node = 2
        while next_node != 0xFFFFFFFF:
            prev_node, next_node, entries = parse_file_list(f, sb, next_node)
            files.extend(entries)
        print(f"[I] Dumping content to {os.path.join(input_file + '.zip')}")
        myzip = zipfile.ZipFile(os.path.join(
            input_file + '.zip'), 'w', compression=zipfile.ZIP_DEFLATED)
        
        # Create directory inode dict
        dirnames = dict()
        for x in tqdm(files, desc="[I] Creating directory tree"):
            if not x.is_dir():
                continue
            if x.name == '.' or x.name == '..':
                continue
            dirnames[x.inode] = (x.name, x.parent_inode)
        dir_cache = dict()

        # Go through each regular file and dump its content
        for x in tqdm(files, desc="[I] Dumping files"):
            if x.is_dir():
                continue
            elif x.parent_inode == root_inode:
                continue

            if x.parent_inode not in dir_cache:
                paths = []
                inode = x.parent_inode
                while inode != root_inode:
                    name, parent = dirnames[inode]
                    paths.append(name)
                    inode = parent
                paths.append(name)
                directory = os.path.join(*reversed(paths))
                dir_cache[x.parent_inode] = directory
            directory = dir_cache[x.parent_inode]
            name = x.name
            if name.startswith('0:'):
                name = name[2:]
            myzip.writestr(os.path.join(root_zip_dir, directory, name), x.data)
        myzip.close()
        print(
            f"[I] Extraction completed successfully {os.path.join(input_file + '.zip')} !")


if __name__ == '__main__':
    parser = ArgumentParser(description='EFS2 extractor')
    parser.add_argument('-i', dest="input_file",
                        type=str, help='Input file (NAND flash dump or EFS2 partition)')
    parser.add_argument('-o', dest="output_dir",
                        type=str, help='Output directory')
    parser.add_argument('-a', "--extract_all", action="store_true",
                        help=' Extract partitions from NAND flash dump and files from EFS2* partitions')
    parser.add_argument('-p', "--extract_parts",
                        action="store_true", help='Extract partitions from NAND flash dump')
    parser.add_argument('-e', "--extract_efs2", action="store_true",
                        help='Extract files from EFS2 partition')

    args = parser.parse_args()

    if not args.input_file:
        sys.exit("[E] Please specify an input file")
    else:
        if not os.path.isfile(args.input_file):
            sys.exit("[E] Please check the input file")

    if not args.output_dir:
        sys.exit("[E] Please specify an output directory")
    else:
        os.makedirs(args.output_dir, exist_ok=True)

    if args.extract_all:
        partitions = extract_nand_partitions(args.input_file, args.output_dir)
        for part in partitions:
            if part["name"].startswith("EFS2"):
                extract_efs2(os.path.join(args.output_dir,
                             part["name"]), args.output_dir)

    elif args.extract_parts:
        extract_nand_partitions(args.input_file, args.output_dir)

    elif args.extract_efs2:
        extract_efs2(args.input_file, args.output_dir)
    else:
        print("[E] Please specify an extract option")
        parser.print_help()
