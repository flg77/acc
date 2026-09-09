# packaging/rpm/acc.spec -- ACC as a package for RHEL 9 / 10 and Fedora (IN-06).
#
# `20260909-acc-install` (proposal 055; operator decisions IN-00 §4b):
#   * the `acc` system user runs the stack and NEVER gains root -- rootless
#     podman under its own sub-uid range, no sudoers entry, NoNewPrivileges
#     in the unit;
#   * layout: /usr/bin/{acc,acc-cli,acc-pkg,acc-tui,acc-webgui,acc-deploy} ->
#     a vendored virtualenv under /usr/lib/acc/venv (Textual, nats-py, LanceDB,
#     pydantic, FastAPI are not in EPEL at the pinned versions -- this is an
#     internal / COPR package, not a Fedora-review one), /etc/acc (the
#     operator's configuration; acc.env 0640 root:acc), /usr/share/acc (the
#     data trees the wheel ships), /var/lib/acc (state, owned by acc);
#   * two channels: the internal Satellite for the spearhead build, COPR fed
#     from the public mirror -- a spearhead build never reaches a public repo.
#
# Build: packaging/rpm/build.sh (builds the wheel, then rpmbuild -ba).
# The wheel is Source0 -- the RPM packages a release artefact, it does not
# re-run the Python build.

# The standard directories, pinned: a build host may carry decorated macros
# (lighthouse: _prefix=/app) and this package lives at the FHS places only.
%global _prefix /usr
%global _exec_prefix /usr
%global _bindir /usr/bin
%global _libdir /usr/lib
%global _datadir /usr/share
%global _sysconfdir /etc
%global _sharedstatedir /var/lib
%global _localstatedir /var
%global acc_venv /usr/lib/acc/venv
# The interpreter command and the package that provides it: RHEL 10 and
# Fedora ship 3.12 as `python3`; RHEL 9 needs the `python3.12` module.
# Override with --define "acc_python python3.12" --define "acc_python_pkg python3.12".
%{!?acc_python: %global acc_python python3}
%{!?acc_python_pkg: %global acc_python_pkg python3}
%{!?acc_pyver: %global acc_pyver %(%{acc_python} -c 'import sys; print("%d.%d" % sys.version_info[:2])')}
%global __brp_mangle_shebangs %{nil}
%global _build_id_links none
%global debug_package %{nil}

Name:           acc
Version:        %{acc_version}
Release:        1%{?dist}
Summary:        Agentic Cell Corpus -- governed agent collectives on the host
License:        Apache-2.0
URL:            https://github.com/flg77/acc
Source0:        agentic_cell_corpus-%{acc_version}-py3-none-any.whl
Source1:        acc-stack.service
Source2:        acc.sysusers.conf
Source3:        acc.tmpfiles.conf

# Not noarch: the vendored venv carries compiled wheels (pydantic-core, lancedb, ...).
BuildRequires:  %{acc_python_pkg}
BuildRequires:  %{acc_python_pkg}-pip
BuildRequires:  systemd-rpm-macros
Requires:       %{acc_python_pkg}
Requires:       podman
Requires:       podman-compose
Requires:       bash
Requires(pre):  shadow-utils
%{?sysusers_requires_compat}
# The vendored virtualenv carries its own dependencies.
AutoReqProv:    no

%description
ACC runs governed agent collectives: one bus, roles as cells, a constitutional
/ setpoint / learned rule layer, per-principal ceilings, an oversight queue, and
signed packages.  This package installs the host commands (acc, acc-cli,
acc-pkg, acc-tui, acc-webgui, acc-deploy), the data trees, the configuration
layout under /etc/acc, the state root under /var/lib/acc, and a systemd unit
that brings the collective up as the unprivileged `acc` user.

%prep
# nothing to unpack: Source0 is a wheel

%build
# nothing to build: the wheel is the release artefact

%install
# A virtualenv at its FINAL path, built inside the buildroot.  --copies keeps
# it independent of the build host's layout; the shebangs are rewritten to
# the final interpreter path so the buildroot prefix never leaks into them.
%{acc_python} -m venv --copies %{buildroot}%{acc_venv}
%{buildroot}%{acc_venv}/bin/python -m pip install --no-cache-dir --no-compile --upgrade pip
# CPU torch FIRST, from PyTorch's CPU index.  `sentence-transformers` (the local
# embedding fallback) is a core dependency and PyPI's `torch` drags in ~5 GB of
# CUDA (nvidia-*, triton) that a host running the collective in containers never
# executes.  Installing the CPU build first leaves the requirement satisfied, so
# the wheel's own resolution never reaches the CUDA one.
%{buildroot}%{acc_venv}/bin/python -m pip install --no-cache-dir --no-compile     --index-url https://download.pytorch.org/whl/cpu torch
%{buildroot}%{acc_venv}/bin/python -m pip install --no-cache-dir --no-compile "%{SOURCE0}[tui]"
# No CUDA may have slipped in behind the CPU build.
if [ -d %{buildroot}%{acc_venv}/lib/python%{acc_pyver}/site-packages/nvidia ]; then
    echo "ERROR: the venv carries CUDA wheels -- the CPU torch pin did not hold" >&2
    exit 1
fi
# buildroot -> final path in scripts and pyvenv.cfg
find %{buildroot}%{acc_venv}/bin -type f -exec sed -i "s|%{buildroot}||g" {} +
sed -i "s|%{buildroot}||g" %{buildroot}%{acc_venv}/pyvenv.cfg
# no pip inside the shipped venv: the package is the only writer
rm -rf %{buildroot}%{acc_venv}/bin/pip* %{buildroot}%{acc_venv}/lib/python%{acc_pyver}/site-packages/pip*

# commands
install -d %{buildroot}%{_bindir}
for cmd in acc acc-cli acc-pkg acc-tui acc-webgui acc-agent acc-catalog; do
    ln -s %{acc_venv}/bin/$cmd %{buildroot}%{_bindir}/$cmd
done

# the data trees: /usr/share/acc is the tree the wheel ships (one copy)
install -d %{buildroot}%{_datadir}
ln -s %{acc_venv}/lib/python%{acc_pyver}/site-packages/acc/_share %{buildroot}%{_datadir}/acc
ln -s %{_datadir}/acc/acc-deploy.sh %{buildroot}%{_bindir}/acc-deploy

# the operator's configuration: the templates, renamed; secrets stay empty
SHARE=%{buildroot}%{acc_venv}/lib/python%{acc_pyver}/site-packages/acc/_share
install -d %{buildroot}%{_sysconfdir}/acc
for f in acc-config.yaml models.yaml collective.yaml catalogs.yaml; do
    install -m 0644 "$SHARE/$f.example" %{buildroot}%{_sysconfdir}/acc/$f
done
install -m 0640 "$SHARE/.env.example" %{buildroot}%{_sysconfdir}/acc/acc.env

# state
install -d %{buildroot}%{_sharedstatedir}/acc/packages
install -d %{buildroot}%{_sharedstatedir}/acc/instances
install -d %{buildroot}%{_sharedstatedir}/acc/workspaces
install -d %{buildroot}%{_sharedstatedir}/acc/logs
install -d %{buildroot}%{_sharedstatedir}/acc/sessions
install -d %{buildroot}%{_sharedstatedir}/acc/trace
install -d %{buildroot}%{_localstatedir}/log/acc

# systemd, sysusers, tmpfiles
install -D -m 0644 %{SOURCE1} %{buildroot}%{_unitdir}/acc-stack.service
install -D -m 0644 %{SOURCE2} %{buildroot}%{_sysusersdir}/acc.conf
install -D -m 0644 %{SOURCE3} %{buildroot}%{_tmpfilesdir}/acc.conf

%pre
%sysusers_create_compat %{SOURCE2}
# rootless podman for the acc user: a sub-uid / sub-gid range, no root, ever.
if ! grep -q '^acc:' /etc/subuid 2>/dev/null; then
    usermod --add-subuids 300000-365535 --add-subgids 300000-365535 acc 2>/dev/null || true
fi

%post
%systemd_post acc-stack.service
%tmpfiles_create %{_tmpfilesdir}/acc.conf
# a session-less system service runs rootless podman only with lingering
loginctl enable-linger acc 2>/dev/null || true
if command -v restorecon >/dev/null 2>&1; then restorecon -R %{_sharedstatedir}/acc %{_localstatedir}/log/acc 2>/dev/null || true; fi

%preun
%systemd_preun acc-stack.service

%postun
%systemd_postun_with_restart acc-stack.service
# /etc/acc and /var/lib/acc are the operator's: never removed

%files
%{acc_venv}
%{_bindir}/acc
%{_bindir}/acc-cli
%{_bindir}/acc-pkg
%{_bindir}/acc-tui
%{_bindir}/acc-webgui
%{_bindir}/acc-agent
%{_bindir}/acc-catalog
%{_bindir}/acc-deploy
%{_datadir}/acc
# 0755: the four *.yaml are 0644 and only reachable through a traversable
# directory -- the operator's own `acc` must read them without joining group
# acc.  The secrets are the one file that stays 0640 root:acc.
%dir %attr(0755,root,acc) %{_sysconfdir}/acc
%config(noreplace) %attr(0644,root,acc) %{_sysconfdir}/acc/acc-config.yaml
%config(noreplace) %attr(0644,root,acc) %{_sysconfdir}/acc/models.yaml
%config(noreplace) %attr(0644,root,acc) %{_sysconfdir}/acc/collective.yaml
%config(noreplace) %attr(0644,root,acc) %{_sysconfdir}/acc/catalogs.yaml
%config(noreplace) %attr(0640,root,acc) %{_sysconfdir}/acc/acc.env
%dir %attr(0750,acc,acc) %{_sharedstatedir}/acc
%dir %attr(0750,acc,acc) %{_sharedstatedir}/acc/packages
%dir %attr(0750,acc,acc) %{_sharedstatedir}/acc/instances
%dir %attr(0750,acc,acc) %{_sharedstatedir}/acc/workspaces
%dir %attr(0750,acc,acc) %{_sharedstatedir}/acc/logs
%dir %attr(0750,acc,acc) %{_sharedstatedir}/acc/sessions
%dir %attr(0750,acc,acc) %{_sharedstatedir}/acc/trace
%dir %attr(0750,acc,acc) %{_localstatedir}/log/acc
%{_unitdir}/acc-stack.service
%{_sysusersdir}/acc.conf
%{_tmpfilesdir}/acc.conf

%changelog
* Wed Sep 09 2026 ACC <flg@nomiras.com> - %{acc_version}-1
- First package: the host commands, the data trees, /etc/acc, /var/lib/acc,
  acc-stack.service as the unprivileged acc user (proposal 055, IN-06).
