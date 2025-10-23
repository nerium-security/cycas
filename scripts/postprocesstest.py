import os
import sys
import argparse

script_path = sys.argv[0]
scriptname = os.path.basename(script_path)
parser = argparse.ArgumentParser(
description = 'This script post-processes zip files containing ',
epilog=f'Example usage: python3 {scriptname} -i *.zip --outputtype csv')
parser.add_argument('-i', '--inputpath', help='Select input zip files.', nargs='+', required=True)
parser.add_argument('-o', '--outputtype', choices=['csv', 'jsonl', 'json'])
parser.add_argument('-f', '--outputfolder', choices=['csv', 'jsonl', 'json'])
parser.add_argument('-a', '--artifacts', default='velociraptor/artifacts/velociraptor_artifacts.json')
args = parser.parse_args()

def main(): 
    None




if __name__ == '__main__':
    main()