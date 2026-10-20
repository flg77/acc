# .copr/Makefile -- how COPR makes the SRPM from a git tag of the public mirror.
#
# COPR's "make srpm" source method clones the repository at the committish the
# package names (a release tag), then runs
#
#     make -f .copr/Makefile srpm outdir="<dir>" spec="<path to the spec>"
#
# in a throw-away chroot that HAS network and in which we are root, so build
# dependencies are installed here.  Everything after that is the same path
# the release pipeline takes on the build host: packaging/rpm/build.sh builds
# the wheel (with the data trees under acc/_share) and pins the version into
# the spec copy that goes into the SRPM, so the SRPM rebuilds in every chroot
# with no --define.  The RPM build itself needs network too (pip in %install):
# the COPR project is created with `--enable-net on`.
#
# Try it locally, no COPR needed:
#
#     make -f .copr/Makefile srpm outdir=/tmp/acc-srpm
#
# A tree that is not exactly the tagged, clean commit gets a `0.<stamp>.g<sha>`
# release (packaging/rpm/version.py): a COPR clone that has lost its tags
# therefore builds a snapshot, never something that impersonates the release.

outdir ?= $(CURDIR)/dist/srpm
spec   ?= packaging/rpm/acc.spec

# The interpreter the SRPM step uses to build the wheel.  COPR's SRPM chroot is
# a current Fedora, where `python3` is 3.12+; on an EL9 workstation set
# PYTHON=python3.12 PYTHON_PKG=python3.12.
PYTHON     ?= python3
PYTHON_PKG ?= python3

.PHONY: srpm deps

srpm: deps
	PYTHON="$(PYTHON)" PYTHON_PKG="$(PYTHON_PKG)" \
	bash packaging/rpm/build.sh --srpm-only --outdir "$(outdir)"

# Only when we may: COPR runs this as root in its chroot; a developer's
# workstation already has these or will say so.
deps:
	@if [ "$$(id -u)" = 0 ] && command -v dnf >/dev/null 2>&1; then \
	    dnf -y install git rpm-build systemd-rpm-macros $(PYTHON_PKG) $(PYTHON_PKG)-pip >/dev/null; \
	fi
	@command -v rpmbuild >/dev/null || { echo "rpmbuild missing (dnf install rpm-build systemd-rpm-macros)" >&2; exit 1; }
	@command -v $(PYTHON) >/dev/null || { echo "$(PYTHON) missing" >&2; exit 1; }
