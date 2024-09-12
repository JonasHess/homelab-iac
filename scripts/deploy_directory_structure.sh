#!/bin/sh
set -e

# Create the directory structure for the homelab
unencrypted
	apps
		homeassistant

encrypted
	private
		pictures
		documents
		others

	media
		audiobooks
		tutorials
		apps
		games
		books
		music
		movies
		series

	public

	apps
		plex
		nzb

	tmp
		download
		watchdir
			nzb
			paperless
