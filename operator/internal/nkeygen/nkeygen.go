// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package nkeygen mints NATS NKey identities (proposal 013, Phase 0c).
//
// It is the operator-side counterpart of acc/nkeys.py.  Implemented on
// top of the Go standard library (crypto/ed25519, encoding/base32) so
// the operator needs no new third-party dependency.
//
// NKey wire format (see github.com/nats-io/nkeys):
//
//   - a *public* key is base32( prefixUser || ed25519Pub(32) || crc16 )
//   - a *seed* is base32( b1 || b2 || ed25519Seed(32) || crc16 ) where
//     b1 = prefixSeed | (prefixUser >> 5) and b2 = (prefixUser & 31) << 5
//
// base32 is RFC 4648 uppercase, no padding; crc16 is the XMODEM
// variant (poly 0x1021, init 0).
package nkeygen

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base32"
	"encoding/binary"
	"fmt"
)

const (
	prefixUser byte = 20 << 3 // 'U'
	prefixSeed byte = 18 << 3 // 'S'
)

var b32 = base32.StdEncoding.WithPadding(base32.NoPadding)

func crc16(data []byte) uint16 {
	var crc uint16
	for _, b := range data {
		crc ^= uint16(b) << 8
		for i := 0; i < 8; i++ {
			if crc&0x8000 != 0 {
				crc = (crc << 1) ^ 0x1021
			} else {
				crc <<= 1
			}
		}
	}
	return crc
}

func encode(prefix, payload []byte) string {
	body := append(append([]byte{}, prefix...), payload...)
	var crc [2]byte
	binary.LittleEndian.PutUint16(crc[:], crc16(body))
	return b32.EncodeToString(append(body, crc[:]...))
}

// GenerateUserNKey mints a fresh NATS user NKey.  Returns
// (seed, public): seed is the secret half (S...-prefixed) and must be
// kept 0600; public is the verifier half (U...-prefixed) for nats.conf.
func GenerateUserNKey() (seed, public string, err error) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return "", "", fmt.Errorf("generate ed25519 key: %w", err)
	}
	public = encode([]byte{prefixUser}, pub)
	b1 := prefixSeed | (prefixUser >> 5)
	b2 := (prefixUser & 31) << 3
	seed = encode([]byte{b1, b2}, priv.Seed())
	return seed, public, nil
}

// PublicFromSeed re-derives the public NKey from a seed string — used
// to rebuild the nats.conf authorization block from the persisted
// Secret without storing public keys separately.
func PublicFromSeed(seed string) (string, error) {
	raw, err := b32.DecodeString(seed)
	if err != nil {
		return "", fmt.Errorf("decode nkey seed: %w", err)
	}
	if len(raw) != 2+ed25519.SeedSize+2 {
		return "", fmt.Errorf("nkey seed: unexpected length %d", len(raw))
	}
	body := raw[:len(raw)-2]
	gotCRC := binary.LittleEndian.Uint16(raw[len(raw)-2:])
	if crc16(body) != gotCRC {
		return "", fmt.Errorf("nkey seed: CRC mismatch")
	}
	edSeed := body[2:] // strip the 2 prefix bytes
	priv := ed25519.NewKeyFromSeed(edSeed)
	pub := priv.Public().(ed25519.PublicKey)
	return encode([]byte{prefixUser}, pub), nil
}
