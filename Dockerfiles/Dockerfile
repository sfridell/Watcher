FROM python:3.11-slim as base

RUN groupadd -r pydevel --gid=1000 && useradd -r -g pydevel --uid=1000 --create-home --shell /bin/bash pydevel

RUN set -ex \
    && RUN_DEPS=" \
    build-essential \
    tk-dev \
    mesa-common-dev \
    wget \
    emacs \
    " \
    && seq 1 8 | xargs -I{} mkdir -p /usr/share/man/man{} \
    && apt-get update && apt-get install -y --no-install-recommends $RUN_DEPS \
    && rm -rf /var/lib/apt/lists/*

RUN \
  wget \
    -O sqlite.tar.gz \
    https://www.sqlite.org/src/tarball/sqlite.tar.gz?r=release \
    && \
    tar xvfz sqlite.tar.gz

RUN \
  ./sqlite/configure --prefix=/usr && \
  make && \
  make install \
  && \
  # Smoke test
  sqlite3 --version

RUN \
  python3 -m pip install "kivy[base]" kivy-examples

RUN \
  apt-get update && apt-get install -y --no-install-recommends git zip unzip openjdk-17-jdk python3-pip autoconf automake libtool libltdl-dev pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev android-sdk vim-tiny yad openssh-client
  
RUN \
  python3 -m pip install --upgrade Cython==0.29.33 virtualenv

RUN \
  python3 -m pip install --upgrade buildozer

RUN \
  python3 -m pip install --upgrade plyer
  
RUN \
  python3 -m pip install --upgrade androidstorage4kivy

RUN \
  python3 -m pip install --upgrade kivymd

RUN \
  python3 -m pip install --upgrade python-lsp-server
RUN \
  mkdir /home/pydevel/.ssh && chown pydevel /home/pydevel/.ssh && chmod 755 /home/pydevel/.ssh
  
USER pydevel
  
RUN \
  git clone https://github.com/sfridell/MyEmacs.git /home/pydevel/.emacs.d

  
