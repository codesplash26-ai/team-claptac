"""Ra-02 / SX1278 SPI driver for a Raspberry Pi. Implements atlas.ingest.lora.LoraLink.

Never transmit without a 433 MHz antenna on the ANT pad. The radio can damage itself.

Wiring (BCM numbers; 3.3 V only):

    Ra-02    Pi
    NSS      GPIO8  (CE0)     pin 24
    RESET    GPIO22           pin 15
    DIO0     GPIO25           pin 22
    SCK      GPIO11           pin 23
    MOSI     GPIO10           pin 19
    MISO     GPIO9            pin 21
    3.3V     3.3V             pin 1
    GND      GND              pin 6
    ANT      433 MHz antenna  required
"""

from __future__ import annotations

import time

# SX1278 registers
_REG_FIFO = 0x00
_REG_OP_MODE = 0x01
_REG_FRF_MSB = 0x06
_REG_FRF_MID = 0x07
_REG_FRF_LSB = 0x08
_REG_PA_CONFIG = 0x09
_REG_LNA = 0x0C
_REG_FIFO_ADDR_PTR = 0x0D
_REG_FIFO_TX_BASE = 0x0E
_REG_FIFO_RX_BASE = 0x0F
_REG_FIFO_RX_CURRENT = 0x10
_REG_IRQ_FLAGS = 0x12
_REG_RX_NB_BYTES = 0x13
_REG_PKT_SNR = 0x19
_REG_PKT_RSSI = 0x1A
_REG_MODEM_CONFIG_1 = 0x1D
_REG_MODEM_CONFIG_2 = 0x1E
_REG_PREAMBLE_MSB = 0x20
_REG_PREAMBLE_LSB = 0x21
_REG_PAYLOAD_LENGTH = 0x22
_REG_MODEM_CONFIG_3 = 0x26
_REG_SYNC_WORD = 0x39
_REG_DIO_MAPPING_1 = 0x40
_REG_VERSION = 0x42
_REG_PA_DAC = 0x4D

_MODE_SLEEP = 0x00
_MODE_STDBY = 0x01
_MODE_TX = 0x03
_MODE_RX_CONTINUOUS = 0x05
_LONG_RANGE = 0x80

_IRQ_TX_DONE = 0x08
_IRQ_PAYLOAD_CRC = 0x20
_IRQ_RX_DONE = 0x40

_BW = {7800: 0, 10400: 1, 15600: 2, 20800: 3, 31250: 4, 41700: 5, 62500: 6, 125000: 7, 250000: 8, 500000: 9}
_FXOSC = 32_000_000


class SX1278Link:
    """Blocking send/receive over a Ra-02. Import only on a Pi with SPI enabled."""

    def __init__(
        self,
        *,
        spi_bus: int = 0,
        spi_device: int = 0,
        reset_pin: int = 22,
        dio0_pin: int = 25,
        frequency_hz: int = 433_000_000,
        spreading_factor: int = 9,
        bandwidth_hz: int = 125_000,
        coding_rate: int = 5,
        sync_word: int = 0x12,
        tx_power_dbm: int = 7,
        preamble: int = 8,
    ) -> None:
        import spidev
        from gpiozero import DigitalInputDevice, DigitalOutputDevice

        if tx_power_dbm > 17:
            raise ValueError("start TX power low; raise only after a range test with an antenna on")
        self.last_rssi_dbm: float | None = None
        self.last_snr_db: float | None = None
        self._reset = DigitalOutputDevice(reset_pin, active_high=True, initial_value=True)
        self._dio0 = DigitalInputDevice(dio0_pin, pull_up=False)
        self._spi = spidev.SpiDev()
        self._spi.open(spi_bus, spi_device)
        self._spi.max_speed_hz = 5_000_000
        self._spi.mode = 0
        self._pulse_reset()
        version = self._read(_REG_VERSION)
        if version != 0x12:
            raise RuntimeError(f"SX1278 not found (version register 0x{version:02x}, expected 0x12)")
        self._write(_REG_OP_MODE, _LONG_RANGE | _MODE_SLEEP)
        time.sleep(0.01)
        self._write(_REG_OP_MODE, _LONG_RANGE | _MODE_STDBY)
        self._set_frequency(frequency_hz)
        self._set_modem(spreading_factor, bandwidth_hz, coding_rate, preamble)
        self._write(_REG_SYNC_WORD, sync_word)
        self._set_tx_power(tx_power_dbm)
        self._write(_REG_LNA, 0x23)
        self._write(_REG_FIFO_TX_BASE, 0x00)
        self._write(_REG_FIFO_RX_BASE, 0x00)
        self._enter_rx()

    def receive(self, timeout_s: float) -> bytes | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            flags = self._read(_REG_IRQ_FLAGS)
            if flags & _IRQ_RX_DONE:
                self._write(_REG_IRQ_FLAGS, 0xFF)
                if flags & _IRQ_PAYLOAD_CRC:
                    self.last_rssi_dbm = self.last_snr_db = None
                    return None
                length = self._read(_REG_RX_NB_BYTES)
                current = self._read(_REG_FIFO_RX_CURRENT)
                self._write(_REG_FIFO_ADDR_PTR, current)
                payload = bytes(self._spi.xfer2([_REG_FIFO] + [0] * length)[1:])
                self._capture_rssi()
                return payload or None
            time.sleep(0.01)
        return None

    def send(self, raw: bytes) -> None:
        if not raw:
            return
        self._standby()
        self._write(_REG_FIFO_ADDR_PTR, 0x00)
        self._spi.xfer2([_REG_FIFO | 0x80] + list(raw))
        self._write(_REG_PAYLOAD_LENGTH, len(raw))
        self._write(_REG_IRQ_FLAGS, 0xFF)
        self._write(_REG_OP_MODE, _LONG_RANGE | _MODE_TX)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self._read(_REG_IRQ_FLAGS) & _IRQ_TX_DONE:
                break
            time.sleep(0.005)
        self._write(_REG_IRQ_FLAGS, 0xFF)
        self._enter_rx()

    def close(self) -> None:
        try:
            self._write(_REG_OP_MODE, _LONG_RANGE | _MODE_SLEEP)
        finally:
            self._spi.close()
            self._reset.close()
            self._dio0.close()

    def _pulse_reset(self) -> None:
        self._reset.off()
        time.sleep(0.01)
        self._reset.on()
        time.sleep(0.01)

    def _set_frequency(self, hz: int) -> None:
        frf = int((hz * (1 << 19)) / _FXOSC)
        self._write(_REG_FRF_MSB, (frf >> 16) & 0xFF)
        self._write(_REG_FRF_MID, (frf >> 8) & 0xFF)
        self._write(_REG_FRF_LSB, frf & 0xFF)

    def _set_modem(self, sf: int, bw_hz: int, cr: int, preamble: int) -> None:
        if sf < 7 or sf > 12:
            raise ValueError("spreading factor must be 7–12")
        bw = _BW.get(bw_hz)
        if bw is None:
            raise ValueError("unsupported bandwidth")
        coding = cr - 4
        if coding < 1 or coding > 4:
            raise ValueError("coding rate must be 5–8 (4/5 … 4/8)")
        self._write(_REG_MODEM_CONFIG_1, (bw << 4) | (coding << 1))
        self._write(_REG_MODEM_CONFIG_2, (sf << 4) | 0x04)  # explicit header + CRC
        symbol_ms = (2**sf) / (bw_hz / 1000)
        ldr = 0x08 if symbol_ms > 16 else 0x00
        self._write(_REG_MODEM_CONFIG_3, ldr | 0x04)
        self._write(_REG_PREAMBLE_MSB, (preamble >> 8) & 0xFF)
        self._write(_REG_PREAMBLE_LSB, preamble & 0xFF)

    def _set_tx_power(self, dbm: int) -> None:
        power = max(2, min(17, dbm))
        self._write(_REG_PA_DAC, 0x84)
        self._write(_REG_PA_CONFIG, 0x80 | (power - 2))

    def _capture_rssi(self) -> None:
        snr_raw = self._read(_REG_PKT_SNR)
        snr = (snr_raw - 256) / 4.0 if snr_raw > 127 else snr_raw / 4.0
        rssi = -164 + self._read(_REG_PKT_RSSI)
        if snr < 0:
            rssi += snr
        self.last_snr_db = round(snr, 1)
        self.last_rssi_dbm = round(rssi, 1)

    def _enter_rx(self) -> None:
        self._write(_REG_DIO_MAPPING_1, 0x00)
        self._write(_REG_IRQ_FLAGS, 0xFF)
        self._write(_REG_OP_MODE, _LONG_RANGE | _MODE_RX_CONTINUOUS)

    def _standby(self) -> None:
        self._write(_REG_OP_MODE, _LONG_RANGE | _MODE_STDBY)
        time.sleep(0.001)

    def _read(self, address: int) -> int:
        return self._spi.xfer2([address & 0x7F, 0])[1]

    def _write(self, address: int, value: int) -> None:
        self._spi.xfer2([address | 0x80, value & 0xFF])
