import datetime as dt
import logging
import time
import typing

import click
import pandas as pd

import srt_utils.process as process


logger = logging.getLogger(__name__)


PAYLOAD_SIZE = 1316
MAXIMUM_SEQUENCE_NUMBER = 2 ** 32


def _nodes_split(ctx, param, value):
    return list(value)


def generate_payload():
    """ 
    Generate payload of PAYLOAD_SIZE size of the following type:
    
    |<------------------- Payload Size ------------------------>|
    +---+---+---+---+---+---+---+---+---+---+---+---+---+   +---+
    | 1 | 2 | 3 |...|255| 1 | 2 | 3 |...|255|...|...|...|...| 0 |
    +---+---+---+---+---+---+---+---+---+---+---+---+---+   +---+
                                                              |
              0 byte at the end indicates the end of payload__/             
    """
    return bytearray([(1 + i % 255) for i in range(0, PAYLOAD_SIZE - 1)]) + bytearray([0])


def insert_srcByte(payload, s):
    """
    Insert SrcByte, SrcTime in packet payload of type
    |<------------------- Payload Size ------------------------>|
    |<-- SrcByte -->|<-- SrcTime -->|                           |
    |    4 bytes    |    4 bytes    |                           |
    +---+---+---+---+---+---+---+---+---+---+---+---+---+   +---+
    | x | x | x | x | x | x | x | x | 9 |10 |...|...|...|...| 0 |
    +---+---+---+---+---+---+---+---+---+---+---+---+---+   +---+
                                                              |
              0 byte at the end indicates the end of payload__/              
    where 
    SrcByte -- Packet Sequence Number applied at the source,
    in units of payload bytes,
    SrcTime -- the time of packet emission from the source,
    in units of payload bytes (not yet implemented).
    Attributes:
        payload: 
            Packet payload,
        s:
            the unique packet sequence number applied at the source,
            in units of messages.          
    """
    payload[0] = s >> 24
    payload[1] = (s >> 16) & 255
    payload[2] = (s >> 8) & 255
    payload[3] = (s >> 0) & 255
    return payload


def calculate_interval(bitrate):
    """ 
    Calculate interval between sending consecutive packets depending on
    desired bitrate, in seconds with microseconds accuracy.
    Attributes:
        bitrate:
            Bitrate, Mbit/s.
    """
    if bitrate is None:
        # Corresponds to 1.05 Mbit/s
        return 0.01
    else:
        return round((PAYLOAD_SIZE * 8) / (bitrate * 1000000), 6)


def calculate_target_time(interval_us):
    # Known issue to consider
    # https://stackoverflow.com/questions/12448592/how-to-add-delta-to-python-datetime-time
    now = dt.datetime.now().time()
    delta = dt.timedelta(microseconds = interval_us)
    target_time = (dt.datetime.combine(dt.date(1,1,1),now) + delta).time()
    return target_time


def print_list(elements):
    for element in elements:
        print(element)


def start_sender(args, interval_s, k):
    """ 
    Start sender (either srt-live-transmit or srt-test-live application) with
    arguments `args` in order to generate and send `k` packets with `interval_s`
    interval between consecutive packets.
    generate packet --> stdin --> SRT
    Examples for debugging purposes as per Section 7 of
    https://tools.ietf.org/html/rfc4737#section-7
    1. Example with a single packet reordered
    sending_order_1 = [1, 2, 3, 5, 6, 7, 8, 4, 9, 10]
    2. Example with two packets reordered
    sending_order_2 = [1, 2, 3, 4, 7, 5, 6, 8, 9, 10]
    3. Example with three packets reordered
    sending_order_3 = [1, 2, 3, 7, 8, 9, 10, 4, 5, 6, 11]
    4. Example with a single packet reordered and two duplicate packets
    sending_order_1_dup = [1, 2, 3, 5, 6, 7, 8, 4, 9, 10, 10, 6]
    """
    if k > MAXIMUM_SEQUENCE_NUMBER:
        logger.error('The number of packets exceeds the maximum possible packet sequence number')

    logger.info('Starting sender')
    proc = process.Process(args)
    proc.start()

    # Sleep for 1s in order to give some time for sender and receiver 
    # to establish the connection
    time.sleep(1)

    payload = generate_payload()
    interval_us = int(interval_s * 1000000)
    assert 0 <= interval_us < 1000000
    
    try:
        for s in range(1, k + 1):
            target_time = calculate_target_time(interval_us)

            logger.debug(f'Sending packet {s}')
            payload_srcByte = insert_srcByte(payload, s)
            proc.process.stdin.write(payload_srcByte)
            proc.process.stdin.flush()

            while (dt.datetime.now().time() < target_time):
                pass
    except KeyboardInterrupt:
        logger.info('KeyboardInterrupt has been caught. Cleaning up ...')
    except Exception as e:
        logger.error(e)
    finally:
        # Sleep for 1s in order to give some time for sender to deliver 
        # the remain portion of packets at the end of experiment
        time.sleep(1)
        logger.info('Stopping sender')
        proc.stop()
        
        logger.info('Collecting sender stdout, stderr')
        stdout, stderr = proc.collect_results()
        print('\nstdout:')
        print_list(stdout)
        print('\nstderr:')
        print_list(stderr)
        print('\n')


def read_data(proc, interval_s):
    """ 
    Read data of `PAYLOAD_SIZE` size from stdout of a process `proc`.
    There are three possible cases:
    - no data in stdout when the transmission has not been started yet
    or has been finished already,
    - there is data, however it's b'', the reason is a possible bug in test 
    application,
    - there is a packet received.
    """
    while True:
        # If there is no data in stdout, the code will hang here
        data = proc.process.stdout.read(PAYLOAD_SIZE)

        if len(data) != 0:
            break

        time.sleep(interval_s)

    return data


def type_p_reordered_ratio_stream(df: pd.DataFrame):
    """ 
    Type-P-Reordered-Ratio-Stream metric as per Section 4.1 of 
    https://tools.ietf.org/html/rfc4737#section-4.1
    The ratio of reordered packets to received packets.
    R = (Count of packets with Type-P-Reordered=TRUE) / (L) * 100,
    where L is the total number of packets received out of K packets sent. 
    Recall that identical copies (duplicates) have been removed, so L <= K. 
    Attributes:
        df:
            Dataframe with received packets information. Duplications
            should be preliminarily removed.
    Returns a tuple of (packets reordered, Type-P-Reordered-Ratio-Stream metric).
    """
    assert df['s@Dst'].is_unique 
    packets_reordered = df['Type-P-Reordered'].sum()
    packets_reordered_metric = round(packets_reordered / len(df.index) * 100, 4)
    return (packets_reordered, packets_reordered_metric)


def sequence_discontinuities(df: pd.DataFrame):
    """ 
    Calculates the number of sequence discontinuities and their 
    total size in packets as per Section 3.4 of 
    https://tools.ietf.org/html/rfc4737#section-3.4
    Recall that identical copies (duplicates) have been removed.
    """
    assert df['s@Dst'].is_unique 
    return (df['Seq Disc'].sum(), df['Seq Disc Size'].sum()) 


def calculate_print_metrics(df: pd.DataFrame, k: int):
    """ 
    Calculates different metrics based on the received packets info
    and prints the results in terminal.
    
    Attributes:
        df:
            `pd.DataFrame` with information regarding received packets
            (containing possible duplicates).
        k:
            Number of packets generated and sent by receiver.
    """
    df_duplicates = df
    packets_received = len(df.index)
    # Remove duplicates from df
    # l does not include duplicated packets, 
    # k is the number of packets sent by receiver
    df.drop_duplicates(subset ='s@Dst', keep = 'first', inplace = True)
    l = len(df.index)
    assert l <= k
    duplicates = packets_received - l
    duplicates_ratio = round(duplicates * 100 / packets_received, 4)
    seq_discontinuities, total_size = sequence_discontinuities(df)
    # This value can be also calculated as the difference between total
    # sequence discontinuities size minus packets reordered, see below
    packets_lost = k - l
    packets_lost_ratio = round(packets_lost * 100 / k, 4)
    packets_reordered, packets_reordered_ratio = type_p_reordered_ratio_stream(df)

    packets_lost_2 = total_size - packets_reordered
    packets_lost_2_ratio = round(packets_lost_2 * 100 / k, 4)

    # data_1 = [
    #     ('Packets Received', packets_received, ),
    #     ('Duplicates', duplicates, duplicates_ratio),
    #     ('Packets Reordered', packets_reordered, packets_reordered_ratio),
    #     ('Packets Lost', packets_lost, packets_lost_ratio)
    # ]
    # df_stats_1 = pd.DataFrame(data_1, columns = ['Metric', 'Number, packet(s)', 'Ratio, %'])

    # data_2 = [('Sequence Discontinuities', seq_discontinuities, total_size)]
    # df_stats_2 = pd.DataFrame(data_2, columns = ['Metric', 'Number', 'Total Size, packet(s)'])

    print(f'Packets Generated (by Sender): {k}')
    print(f'Packets Received: {packets_received}')
    print(f'Duplicates: {duplicates}')
    print(f'Packets Reordered: {packets_reordered}')
    print(f'Sequence Discontinuities: {seq_discontinuities}, Total Size: {total_size} packet(s)')
    print(f'Packets Lost (Generated - Received - Duplicates): {packets_lost}')
    print(f'Packets Lost (Total Size of Sequence Discontinuities - Reordered): {packets_lost_2}')
    print(f'Duplicates Ratio: {duplicates_ratio} %')
    print(f'Reordered Packets Ratio: {packets_reordered_ratio} %')
    print(f'Lost Packets Ratio (Generated - Received - Duplicates): {packets_lost_ratio} %')
    print(f'Lost Packets Ratio (Total Size of Sequence Discontinuities - Reordered): {packets_lost_2_ratio} %')
    print('\n')

    logger.info(
        'Writing results to a set of .csv files: packets_duplicates.csv, '
        'packets_no_duplicates.csv'
    )
    df_duplicates.to_csv('packets_duplicates.csv')
    df.to_csv('packets_no_duplicates.csv')
    logger.info('Writing to .csv is finished')


def start_receiver(args, interval_s, k):
    """ 
    Start receiver (either srt-live-transmit or srt-test-live application) with
    arguments `args` in order to receive `k` packets that have been sent by 
    a sender with `interval_s` interval between consecutive packets and analyze
    received data knowing the algorithm of packets generation at a sender side.
    SRT --> stdout --> analyze received packets
    """
    if k > MAXIMUM_SEQUENCE_NUMBER:
        logger.error('The number of packets exceeds the maximum possible packet sequence number')

    logger.info('Starting receiver')
    proc = process.Process(args)
    proc.start()

    logger.info(
        'Please start a sender with 1) the same value of n or duration '
        'and bitrate, 2) the same attributes ...'
    )

    payload = generate_payload()
    # NextExp -- the next expected sequence number at the destination,
    # in units of messages. The stored value in NextExp is determined 
    # from a previously arriving packet.
    next_exp = 1
    # List of dictionaries for storing received packets info
    dicts = []

    try:
        # NOTE: On one hand, the number of actually arrived packets can be less then
        # the number of sent packets k because of losses; on the other hand,
        # duplicates can make it greater than k. As of now, we will stop the experiment
        # once k packets are received, however some percentage of k can be introduced
        # for checking the possibility of receiving duplicate packets.
        for i in range(1, k + 1):
            received_packet = read_data(proc, interval_s)
            src_byte = received_packet[:4]
            s = int.from_bytes(src_byte, byteorder='big')
            logger.debug(f'Received packet {s}')
            src_byte = src_byte.hex()
            previous_next_exp = next_exp

            if s >= next_exp:
                # If s >= next_exp, packet s is in-order. In this case, next_exp
                # is set to s+1 for comparison with the next packet to arrive.
                if s > next_exp:
                    # Some packets in the original sequence have not yet arrived,
                    # and there is a sequence discontinuity assotiated with packet s.
                    # The size of this discontinuty is s-next_exp, equal to the 
                    # number of packets presently missing, either reordered or lost.
                    seq_discontinuty = True
                    seq_discontinuty_size = s - next_exp
                else:
                    # When s = next_exp, the original sequence has been maintained,
                    # and there is no discontinuty present. 
                    seq_discontinuty = False
                next_exp = s + 1
                type_p_reordered = False
            else:  
                # When s < next_exp, the packet is reordered. In this case the
                # next_exp value does not change.
                type_p_reordered = True
                seq_discontinuty = False

            if not seq_discontinuty:
                seq_discontinuty_size = 0

            dicts += [{
                's@Dst': s,
                'NextExp': previous_next_exp,
                'SrcByte (hex)': src_byte,
                'Dst Order': i,
                'Type-P-Reordered': type_p_reordered,
                'Seq Disc': seq_discontinuty,
                'Seq Disc Size': seq_discontinuty_size,
            }]
    except KeyboardInterrupt:
        logger.info('KeyboardInterrupt has been caught. Cleaning up ...')
    finally:
        logger.info('Stopping receiver')
        proc.stop()

        logger.info('Collecting receiver stdout, stderr')
        stdout, stderr = proc.collect_results()
        print('\nstdout:')
        print_list(stdout)
        print('\nstderr:')
        print_list(stderr)
        print('\n')

        if len(dicts) == 0:
            logger.info('No packets received')
            return

        logger.info('Experiment results: \n')
        df = pd.DataFrame(dicts)
        calculate_print_metrics(df, k)


@click.group()
@click.option(
    '--debug/--no-debug',
    default=False,
    help='Activate DEBUG level of script logs'
)
def cli(debug):
    if debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)-15s [%(levelname)s] %(message)s',
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)-15s [%(levelname)s] %(message)s',
        )


@cli.command()
@click.option(
    '--ip', 
    default='127.0.0.1', 
    help='IP to call', 
    show_default=True
)
@click.option(
    '--port', 
    default=4200, 
    help='Port to call', 
    show_default=True
)
@click.option(
    '--duration', 
    default=60, 
    help='Duration, s', 
    show_default=True
)
@click.option(
    '--n', 
    help='Number of packets', 
    type=int
)
@click.option(
    '--bitrate', 
    help='Bitrate, Mbit/s', 
    type=float
)
@click.option(
    '--attrs',
    help='SRT attributes to pass within query. Format: "key1=value1&key2=value2"'
)
@click.argument(
    'path', 
    type=click.Path(exists=True)
)
def sender(ip, port, duration, n, bitrate, attrs, path):
    srt_str = f'srt://{ip}:{port}'
    if attrs:
        srt_str += f'?{attrs}'
    args = [
        f'{path}',
        'file://con',
        srt_str,
        # '-v', 
        # '-loglevel:error'
    ]
    interval = calculate_interval(bitrate)
    if n is None:
        n = int(duration // interval) + 1

    logger.info(f'interval: {interval}, n: {n}')
    logger.info(f'args: {args}')
    start_sender(args, interval, n)


@cli.command()
@click.option(
    '--port', 
    default=4200, 
    help='Port to listen', 
    show_default=True
)
@click.option(
    '--duration', 
    default=60, 
    help='Duration, s', 
    show_default=True
)
@click.option(
    '--n', 
    help='Number of packets', 
    type=int
)
@click.option(
    '--bitrate', 
    help='Bitrate, Mbit/s', 
    type=float
)
@click.option(
    '--attrs',
    help='SRT attributes to pass within query. Format: "key1=value1&key2=value2"'
)
@click.argument(
    'path', 
    type=click.Path(exists=True)
)
def receiver(port, duration, n, bitrate, attrs, path):
    srt_str = f'srt://:{port}'
    if attrs:
        srt_str += f'?{attrs}'
    args = [
        f'{path}',
        srt_str,
        'file://con',
        # '-v', 
        # '-loglevel:error'
    ]
    interval = calculate_interval(bitrate)
    if n is None:
        n = int(duration // interval) + 1

    logger.info(f'interval: {interval}, n: {n}')
    logger.info(f'args: {args}')
    start_receiver(args, interval, n)


@cli.command()
@click.option(
    '--node',
    help='host:port combination, multiple nodes can be defined',
    required=True,
    multiple=True,
    callback=_nodes_split
)
@click.option(
    '--duration',
    default=60,
    help='Duration, s',
    show_default=True
)
@click.option(
    '--n',
    help='Number of packets',
    type=int
)
@click.option(
    '--bitrate',
    help='Bitrate, Mbit/s',
    type=float
)
@click.option(
    '--attrs',
    help='SRT attributes to pass within query. Format: "key1=value1&key2=value2"'
)
@click.option(
    '--ll', 
    type=click.Choice(['fatal', 'error', 'note', 'warning', 'debug']), 
    default='debug',
    help='Minimum severity for logs',
    show_default=True
)
@click.option(
    '--lfa',
    help='Enabled functional areas for logs, multiple areas can be defined',
    multiple=True
)
@click.option(
    '--lf', 
    type=click.Path(),
    help='File to send logs to'
)
@click.argument(
    'path', 
    type=click.Path(exists=True)
)
def re_sender(node, duration, n, bitrate, attrs, ll, lfa, lf, path):
    # sender, caller
    # ../srt/srt-ethouris/_build/srt-test-live file://con -g srt://*?type=redundancy 127.0.0.1:4200
    # TODO: type=redundancy has changed to type=broadcast, test this properly once the URL format for
    # srt-test-live application is fixed
    srt_str = f'srt://*'
    if attrs:
        srt_str += f'?{attrs}'
    args = [
        f'{path}',
        'file://con',
        '-g',
        srt_str,
    ]
    args += node
    if lf:
        args += [
            '-ll', ll,
            '-lf', lf,
        ]
        if lfa:
            args += ['-lfa']
            args += lfa
    interval = calculate_interval(bitrate)
    if n is None:
        n = int(duration // interval) + 1

    logger.info(f'interval: {interval}, n: {n}')
    logger.info(f'args: {args}')
    start_sender(args, interval, n)


@cli.command()
@click.option(
    '--port',
    default=4200,
    help='Port to listen',
    show_default=True
)
@click.option(
    '--duration',
    default=60,
    help='Duration, s',
    show_default=True
)
@click.option(
    '--n',
    help='Number of packets',
    type=int
)
@click.option(
    '--bitrate',
    help='Bitrate, Mbit/s',
    type=float
)
@click.option(
    '--attrs',
    help='SRT attributes to pass within query. Format: "key1=value1&key2=value2"'
)
@click.option(
    '--ll', 
    type=click.Choice(['fatal', 'error', 'note', 'warning', 'debug']), 
    default='debug',
    help='Minimum severity for logs',
    show_default=True
)
@click.option(
    '--lfa',
    help='Enabled functional areas for logs, multiple areas can be defined',
    multiple=True
)
@click.option(
    '--lf', 
    type=click.Path(),
    help='File to send logs to'
)
@click.argument(
    'path', 
    type=click.Path(exists=True)
)
def re_receiver(port, duration, n, bitrate, attrs, ll, lfa, lf, path):
    # receiver, listener
    # ../srt/srt-ethouris/_build/srt-test-live srt://:4200?groupconnect=true file://con
    # TODO: groupconnect=true changed to groupconnect=1, test this additionally once
    # the URL format for srt-test-live application is fixed
    srt_str = f'srt://:{port}'
    if attrs:
        srt_str += f'?{attrs}'
    args = [
        f'{path}',
        srt_str,
        'file://con',
    ]
    if lf:
        args += [
            '-ll', ll,
            '-lf', lf,
        ]
        if lfa:
            args += ['-lfa']
            args += lfa
    interval = calculate_interval(bitrate)
    if n is None:
        n = int(duration // interval) + 1

    logger.info(f'interval: {interval}, n: {n}')
    logger.info(f'args: {args}')
    start_receiver(args, interval, n)


if __name__ == '__main__':
    cli()
