# efs2_extractor
# Description
This script aims at providing a convenient way to extract and parse EFS2 partitions from a NAND flash dump of a Qualcomm based device (e.g. Quectel BG96)

The flash layout might need some adjustement depending on the flash dumped

# Usage

```
usage: efs2_extractor.py [-h] [-i INPUT_FILE] [-o OUTPUT_DIR] [-a] [-p] [-e]

EFS2 extractor

options:
  -h, --help           show this help message and exit
  -i INPUT_FILE        Input file (NAND flash dump or EFS2 partition)
  -o OUTPUT_DIR        Output directory
  -a, --extract_all    Extract partitions from NAND flash dump and files from EFS2* partitions
  -p, --extract_parts  Extract partitions from NAND flash dump
  -e, --extract_efs2   Extract files from EFS2 partition
```
# Inner workings
## Partition table identification

The script iterates over a NAND flash dump searching for a valid partition table containing the following magic:

```
magic1 = b'\xaa\x73\xee\x55'
magic2 = b'\xdb\xbd\x5e\xe3'
```
Each partitions will be extracted to the specified output directory.

## EFS2 superblock identification
The script iterates over each superblock available in the EFS2 partition to find the one with the highest `age`

The superblocks can be identified by the following magic (`EFSSuper`): 

```
magic1 = b'\x45\x46\x53\x53'
magic2 = b'\x75\x70\x65\x72'
```
The superblock provides information about the EFS2 filesystem such as the block/page size and page table location

## EFS2 page table
The page table entry [`3`] contains the EFS info block which allows to identify the root inode of the filesystem

The page table entry [`2`] points to the first entry of the node linked list

By iterating and parsing the whole linked list, the filesystem structure and content can be extracted to a zip file

# Credits
* This work was based on leaked sources available from a public GitHub [repository](https://github.com/sahthi/somebackup)
* Special thanks to Roman V. for his awesome contribution