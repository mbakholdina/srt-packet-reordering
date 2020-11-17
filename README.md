# srt-packet-reordering

Script designed to evaluate possible packet reordering, duplicates, sequence discontinuities and packet loss in [SRT](https://github.com/Haivision/srt) as per [RFC 4737 - Packet Reordering Metrics](https://tools.ietf.org/html/rfc4737).

The idea of the script is the following:

1. On a sender side, generate and send via stdin `k` packets of `Payload Size = 1316 bytes` with the following payload structure
```
|<------------------- Payload Size ------------------------>|
|<-- SrcByte -->|<-- SrcTime -->|                           |
|    4 bytes    |    4 bytes    |                           |
+---+---+---+---+---+---+---+---+---+---+---+---+---+   +---+
| x | x | x | x | x | x | x | x | 9 |10 |...|...|...|...| 0 |
+---+---+---+---+---+---+---+---+---+---+---+---+---+   +---+
                                                          |
          0 byte at the end indicates the end of payload__/
```
where `SrcByte` -- Packet Sequence Number applied at the source,
in units of payload bytes,
`SrcTime` -- the time of packet emission from the source,
in units of payload bytes (not yet implemented).

2. On a receiver side, receive and read the data from stdout, then validate received packets for possible packet reordering, duplicates, sequence discontinuities and packet loss.

3. The pipeline is as follows: file://con {stdin of testing application} -> SRT -> file://con {stdout of testing application}.

The original idea is coming from the following [PR #663](https://github.com/Haivision/srt/pull/663).


## Metrics supported

### Type-P-Reordered-Ratio-Stream

Given a stream of packets sent from a source to a destination, the ratio of reordered packets in the sample is 

```
R = (Count of packets with Type-P-Reordered=TRUE) / ( L ) * 100
```

where `L` is the total number of packets received out of the `K` packets sent. Recall that identical copies (duplicates) have been removed, so L <= K.

Note 1: If duplicate packets (multiple non-corrupt copies) arrive at the destination, they MUST be noted, and only the first to arrive is considered for further analysis (copies would be declared reordered packets).  

Note 2: Let k be a positive integer equal to the number of packets sent. Let l be a non-negative integer representing the number of packets that were received out of the k packets sent. Note that there is no relationship between k and l: on one hand, losses can make l less than k; on the other hand, duplicates can make l greater than k.


## Getting Started

### Requirements

* python 3.6+
* SRT `srt-test-live` test application built on both receiver and sender side

**Important:** `srt-live-transmit` test application is not going to be supported by the script. It has an issue related to not flushing data at the end of experiment which were partially fixed in [PR #663](https://github.com/Haivision/srt/pull/663). If this support is required, `sender` and `receiver` subcommands can be used for testing purposes, but:
- at the very end of experiment receiver will not get all the packets and there would be a need to interrupt it by Ctrl-C;
- `srt-live-transmit` by default has payload size = 1440 bytes, we need 1316 bytes, but it can be adjusted by means of using appropriate option,
- it's recommended to adjust buffer size to 1 packet insted of 10 by default.

To install python libraries use:
```
pip install -r requirements.txt
```


## Running Script

Please use `--help` option in order to get the full list of available options and sub-commands.
```
Usage: packet_reordering.py [OPTIONS] COMMAND [ARGS]...

Options:
  --debug / --no-debug  Activate DEBUG level of script logs
  --help                Show this message and exit.

Commands:
  re-receiver
  re-sender
  receiver
  sender
```

`re-receiver` and `re-sender` sub-commands are designed for Connection Bonding testing and should be used with `srt-test-live` testing application.

`receiver` and `sender` sub-commands are designed for the general use cases and should be used with `srt-test-live` testing application. `srt-live-transmit` is supported, but not recommended for usage.

Please take into consideration that a receiver should be started first, then as soon as you get the following message in a terminal
```
2019-09-02 15:39:14,052 [INFO] Please start a sender with 1) the same value of n or duration and bitrate, 2) with the same attributes ...
```
you can start a sender and a transmission via SRT will happen. Note, that both receiver and sender should be started with 1) the same values of n or duration and bitrate, 2) the same attributes. See examples below.

Use `--debug` option to activate the DEBUG level of script logs. In case of sender, it will print all the packets sent to the receiver in real time, e.g.,
```
...
2020-03-19 14:50:30,012 [DEBUG] Sending packet 9455
2020-03-19 14:50:30,013 [DEBUG] Sending packet 9456
2020-03-19 14:50:30,014 [DEBUG] Sending packet 9457
...
```

In case of receiver, all the packets received.

**Important to know:** Once the transmission is finished, both sender and receiver will be stopped. However there is an opportunity for receiver to hang in case not all the sent packets are received. As of now, use `Ctrl-C` to interrupt the script. See section {#receiver-stop-condition}.

**Important to consider that:** 1) receiver mode = listener, sender mode = caller; 2) network impairements should be introduced properly, e.g., if receiver is started at Endpoint B and sender - at Endpoint A, than network impairements like packet reordering, packet loss, etc., should be introduced at Endpoint A. However it depends on the network impairments scenario. If you would like to introduce packet loss on the way back (from receiver to sender), please setup packet loss on Endpoint B as well.

Use `--help` to get the list of available options for a particular sub-command
```
python test_packet_reordering.py re-receiver --help
```
```
Usage: packet_reordering.py re-receiver [OPTIONS] PATH

Options:
  --port INTEGER                  Port to listen  [default: 4200]
  --duration INTEGER              Duration, s  [default: 60]
  --n INTEGER                     Number of packets
  --bitrate FLOAT                 Bitrate, Mbit/s
  --attrs TEXT                    SRT attributes to pass within query. Format:
                                  "key1=value1&key2=value2"
  --ll [fatal|error|note|warning|debug]
                                  Minimum severity for logs  [default: debug]
  --lfa TEXT                      Enabled functional areas for logs, multiple
                                  areas can be defined
  --lf PATH                       File to send logs to
  --help                          Show this message and exit.
```

where `PATH` is the path to an appropriate testing application.

### Script Commands for Connection Bonding Testing

#### Running receiver and sender locally

Start the receiver first. Notice that `attrs` contains `groupconnect=1` as the first attribute-value pair which is connection bonding related and application dependent setting. The following attributes like latency `latency`, buffer sizes `sndbuf` and `rcvbuf`, flow control `fc` are SRT related attributes.
```
python packet_reordering.py re-receiver --duration 180 --bitrate 10 --attrs "groupconnect=1&latency=200&sndbuf=125000000&rcvbuf=125000000&fc=60000" ../srt-hai-bonding/_build/srt-test-live
```

Next start the sender. Use the same values of n or duration as well as the same SRT attributes (all the above except `groupconnect=1`). Notice that the first attribute-value pair here is `type=broadcast` which is connection bonding related and application dependent setting. It sets the connection bonding mode equal to "broadcast".
``` 
python packet_reordering.py re-sender --duration 180 --bitrate 10 --attrs "type=broadcast&latency=200&sndbuf=125000000&rcvbuf=125000000&fc=60000" --node 127.0.0.1:4200 ../srt-hai-bonding/_build/srt-test-live
```

For main/backup mode, set `type=backup`.

#### Running receiver and sender on two machines

Please follow the same steps as described above.
```
python packet_reordering.py re-receiver --duration 180 --bitrate 10 --attrs "groupconnect=1&latency=200&sndbuf=125000000&rcvbuf=125000000&fc=60000" ../srt/srt-ethouris/_build/srt-test-live
```
```
python packet_reordering.py re-sender --duration 180 --bitrate 10 --attrs "type=broadcast&latency=200&sndbuf=125000000&rcvbuf=125000000&fc=60000" --node 192.168.2.1:4200 --node 192.168.3.1:4200 ../srt/srt-ethouris/_build/srt-test-live
```

#### Debugging

Use `--ll`, `--lfa`, `--lf` options to get logs from test-application for the purposes of debugging. In this case, make sure that `srt-test-live` application has been built with `-DENABLE_HEAVY_LOGGING=ON` enabled.

`--lfa` option of the application allows to pass one or several values. Use `--lfa ~all` to pass only one value (in this case all the logs will be disabled) or `--lfa ~all --lfa cc` to pass several values (`~all` and `cc` in this case, all the logs will be disabled except `cc` logs). When passing several values, it's important to use `--lfa` option multiple times.

**Important to know:** Logs capturing affects the speed of data packets receiving which may result in a pretty big sequence number difference between received and sent packets (more than 1000 when usually it is around 100-200). It also affects the process of data receiving and results in appearance of sequence discontinuities and lost packets. It is expected behaviour and most probably related to the absence of free space in receiving buffer while producing log messages by the protocol.

**Commands examples:**
```
python packet_reordering.py --debug re-receiver --duration 10 --bitrate 10 --attrs "groupconnect=1&latency=200&sndbuf=125000000&rcvbuf=125000000&fc=60000" --ll debug --lf rcv-logs.txt --lfa ~all --lfa cc ../srt-ethouris/_build/srt-test-live
```

```
python packet_reordering.py --debug re-sender --duration 10 --bitrate 10 --attrs "type=broadcast&latency=200&sndbuf=125000000&rcvbuf=125000000&fc=60000" --node 127.0.0.1:4200 --ll debug --lf snd-logs.txt --lfa ~all --lfa cc ../srt-ethouris/_build/srt-test-live
```

Note that `--debug` option is used here to activate the DEBUG level of script logs. `--ll debug` may be omitted, because it's default value of `--ll` option.

<!-- As of now `stderr` of test application is not captured, so you can see the messages in a terminal as well as script's log messages. In order to capture all these messages to a file add `2>&1 | tee filepath` or `2>filepath` postfix to a command. -->

### Script Commands for General Use Case

The way of using the script for general use case is the same as described in Section "Script Commands for Connection Bonding Testing".

Here is an example of commands:

```
# Receiver
python packet_reordering.py receiver --bitrate 1 --attrs "latency=400" ../srt-mbakholdina/_build/srt-test-live
# Sender
python packet_reordering.py sender --bitrate 1 --ip 192.168.2.2 --attrs "latency=400" ../srt-mbakholdina/_build/srt-test-live
```

### Script Output

An example of receiver terminal output is provided below:

```
Packets Generated (by Sender): 9497
Packets Received: 9497
Duplicates: 0
Packets Reordered: 0
Sequence Discontinuities: 0, Total Size: 0 packet(s)
Packets Lost (Generated - Received - Duplicates): 0
Packets Lost (Total Size of Sequence Discontinuities - Reordered): 0
Duplicates Ratio: 0.0 %
Reordered Packets Ratio: 0.0 %
Lost Packets Ratio (Generated - Received - Duplicates): 0.0 %
Lost Packets Ratio (Total Size of Sequence Discontinuities - Reordered): 0.0 %
```

At the same time, the lists of received packets (with duplicates and without duplicates) are saved in `.csv` files in the root folder:
```
packets_duplicates.csv
packets_no_duplicates.csv
```

### Receiver Stop Condition {#receiver-stop-condition}

Let `k` be a positive integer equal to the number of packets sent. Let `l` be a non-negative integer representing the number of packets that were received out of the `k` packets sent. Note that there is no relationship between `k` and `l`: on one hand, losses can make `l` less than `k`; on the other hand, duplicates can make `l` greater than `k`.

As of now, the stop condition for the receiver is the following: wait for `k` packets being received and then terminate the process. So,

1. If there are packets lost (`l < k`), the receiver will hang trying to receive all generated by the sender packets `k`. In this case, use `CTRL-C` to interrupt the receiver manually.

2. If there are duplicated packets (`l > k`), there is a chance to get and register not all the possible duplicates because the receiver will be stopped once `k` packets are received. Everything else coming after `k` packets will not be received and registered. Some percentage of `k`, let's say 5%, can be introduced to improve this.


## Notes

### Note 1 - nakreport=0&linger=0 in SRT URL

The idea behid adding `nakreport=0&linger=0` in SRT URL was the following:

1. nakreport=0 disables periodic NAK report

With the Periodic NAK report enabled the sender always waits for the NAK report from the receiver, and does not attempt retransmissions on its own.

If the sender sends the very last packet, and it happens to be lost during the transmission, the receiver will not be able to detect this situation. Therefore both sender and receiver will be hanging waiting for messages from each other.

2. linger=0 disables linger

When linger is enabled, SRT socket will be waiting for delivery of all packets in the sending buffer before closing itself.
Together with Periodic NAK report enabled behavior in the above case (lost the very last data packet) this will lead to a default hangup for 3 minutes (default linger timeout).

In the latest versions of SRT (roughly v1.4.0+) the default value for linger in live mode is 0 by default.

These URI options are currently removed for all sub-commands.


## ToDo

* Add passing SRT options through a command line,
* Add writing stdout, stderr of the processes to files instead of terminal,
* Instead of printing result dataframe with packets data, print pieces of this dataframe with problem places,
* If possible speed up data packets receiving at a receiver side,
* Integrate the script in the CI/CD pipeline, [PR #663](https://github.com/Haivision/srt/pull/663) to start with,
* Implement SrcTime (the time of packet emission from the source) inserted in a packet payload in order to be able to calculate DstTime, Delay, LateTime and other metrics related to sending and receiving packet times as per [RFC 4737 - Packet Reordering Metrics](https://tools.ietf.org/html/rfc4737),
* Implement n-reordering metric calculation as per [Section 5 of RFC 4737  - Packet Reordering Metrics](https://tools.ietf.org/html/rfc4737#section-5),
* Improve receiver stop condition in order to introduce some persentage of `k` packets to be able to receive all the possible duplicated packets, where `k` is the number of packets sent by sender.
