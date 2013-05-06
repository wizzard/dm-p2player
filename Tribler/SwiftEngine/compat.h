/*
 *  compat.h
 *  compatibility wrappers
 *
 *  Created by Arno Bakker, Victor Grishchenko
 *  Copyright 2009-2012 TECHNISCHE UNIVERSITEIT DELFT. All rights reserved.
 *
 */
#ifndef SWIFT_COMPAT_H
#define SWIFT_COMPAT_H

#ifdef _MSC_VER
typedef unsigned char uint8_t;
typedef signed char int8_t;
typedef unsigned short uint16_t;
typedef short int16_t;
typedef unsigned int uint32_t;
typedef int int32_t;
typedef __int64 int64_t;
typedef unsigned __int64 uint64_t;
#else
#include <stdint.h>
#endif

#ifdef _WIN32
#include <winsock2.h>
#include <sys/stat.h>
#include <io.h>
#include <xutility> // for std::min/max
#else
#include <sys/mman.h>
#include <arpa/inet.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#endif

#include <fcntl.h>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <errno.h>

#ifdef _MSC_VER
#include "getopt_win.h"
#else
#include <getopt.h>
#endif

#ifdef _WIN32
#define open(a,b,c)    _open(a,b,c)
#define strcasecmp	   stricmp
#define strtok_r	   strtok_s
#define stat(a,b)      _stat(a,b)
#endif
#ifndef S_IRUSR
#define S_IRUSR _S_IREAD
#endif
#ifndef S_IWUSR
#define S_IWUSR _S_IWRITE
#endif
#ifndef S_IRGRP
#define S_IRGRP _S_IREAD
#endif
#ifndef S_IROTH
#define S_IROTH _S_IREAD
#endif

#ifdef _WIN32
typedef char* setsockoptptr_t;
typedef int socklen_t;
#else
typedef void* setsockoptptr_t;
#endif

// libevent2 assumes WIN32 is defined
#ifdef _WIN32
#define WIN32	_WIN32
#endif
#include <event2/util.h>

#ifndef _WIN32
#define INVALID_SOCKET -1
#endif

#ifndef LONG_MAX
#include <limits>
#define LONG_MAX	numeric_limits<int>::max()
#endif

#ifdef _WIN32
// log2 is C99 which is not fully supported by MS VS
#define log2(x)		(log(x)/log(2.0))
#endif


namespace swift {

/** tint is the time integer type; microsecond-precise. */
typedef int64_t tint;
#define TINT_HOUR ((swift::tint)1000000*60*60)
#define TINT_MIN ((swift::tint)1000000*60)
#define TINT_SEC ((swift::tint)1000000)
#define TINT_MSEC ((swift::tint)1000)
#define TINT_uSEC ((swift::tint)1)
#define TINT_NEVER ((swift::tint)0x3fffffffffffffffLL)

#ifdef _WIN32
#define tintabs	_abs64
#else
#define tintabs	::abs
#endif


size_t  file_size (int fd);

int     file_seek (int fd, size_t offset);

int     file_resize (int fd, size_t new_size);

void*   memory_map (int fd, size_t size=0);
void    memory_unmap (int fd, void*, size_t size);

void    print_error (const char* msg);

#ifdef _WIN32

/** UNIX pread approximation. Does change file pointer. Is not thread-safe */
size_t  pread(int fildes, void *buf, size_t nbyte, long offset);

/** UNIX pwrite approximation. Does change file pointer. Is not thread-safe */
size_t  pwrite(int fildes, const void *buf, size_t nbyte, long offset);

int     inet_aton(const char *cp, struct in_addr *inp);

#endif

std::string gettmpdir(void);

tint    usec_time ();

bool    make_socket_nonblocking(evutil_socket_t s);

bool    close_socket (evutil_socket_t sock);

struct timeval* tint2tv (tint t);


};

#endif

