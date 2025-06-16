"""
Tool to monitor home network
"""
import argparse
import sys
import io

def get_args(argv):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    parser_clients = subparsers.add_parser('clients')
    parser_clients_commands = parser_spirits.add_subparsers(dest='clients_command')
    parser_clients_list = parser_clients_commands.add_parser('list')

    return parser.parse_args(argv)


# Main program -- if invoked from command line, otherwise, an entry point for UI
def process_command(argv = sys.argv[1:]):
    args = get_args(argv)
    output = io.StringIO()
    
    if args.command == 'clients':
        if args.spirits_command == 'list':
            list_clients(file=output)
                
    return output

if __name__ == '__main__':
    print(process_command().getvalue())

