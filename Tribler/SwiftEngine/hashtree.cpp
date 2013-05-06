/*
 *  hashtree.cpp
 *  serp++
 *
 *  Created by Victor Grishchenko on 3/6/09.
 *  Copyright 2009-2012 TECHNISCHE UNIVERSITEIT DELFT. All rights reserved.
 *
 */

#include "hashtree.h"
#include "bin_utils.h"
//#include <openssl/sha.h>
#include "sha1.h"
#include <cassert>
#include <cstring>
#include <cstdlib>
#include <fcntl.h>
#include "compat.h"
#include "swift.h"

#include <iostream>

#ifdef _WIN32
#define OPENFLAGS         O_RDWR|O_CREAT|_O_BINARY
#else
#define OPENFLAGS         O_RDWR|O_CREAT
#include <sys/stat.h>
#endif


using namespace swift;

const Sha1Hash Sha1Hash::ZERO = Sha1Hash();

void SHA1 (const void *data, size_t length, unsigned char *hash) {
    blk_SHA_CTX ctx;
    blk_SHA1_Init(&ctx);
    blk_SHA1_Update(&ctx, data, length);
    blk_SHA1_Final(hash, &ctx);
}

Sha1Hash::Sha1Hash(const Sha1Hash& left, const Sha1Hash& right) {
    blk_SHA_CTX ctx;
    blk_SHA1_Init(&ctx);
    blk_SHA1_Update(&ctx, left.bits,SIZE);
    blk_SHA1_Update(&ctx, right.bits,SIZE);
    blk_SHA1_Final(bits, &ctx);
}

Sha1Hash::Sha1Hash(const char* data, size_t length) {
    if (length==-1)
        length = strlen(data);
    SHA1((unsigned char*)data,length,bits);
}

Sha1Hash::Sha1Hash(const uint8_t* data, size_t length) {
    SHA1(data,length,bits);
}

Sha1Hash::Sha1Hash(bool hex, const char* hash) {
    if (hex) {
        int val;
        for(int i=0; i<SIZE; i++) {
            if (sscanf(hash+i*2, "%2x", &val)!=1) {
                memset(bits,0,20);
                return;
            }
            bits[i] = val;
        }
        assert(this->hex()==std::string(hash));
    } else
        memcpy(bits,hash,SIZE);
}

std::string    Sha1Hash::hex() const {
    char hex[HASHSZ*2+1];
    for(int i=0; i<HASHSZ; i++)
        sprintf(hex+i*2, "%02x", (int)(unsigned char)bits[i]);
    return std::string(hex,HASHSZ*2);
}



/**     H a s h   t r e e       */


HashTree::HashTree (const char* filename, const Sha1Hash& root_hash, size_t chunk_size, const char* hash_filename, bool check_hashes, const char* binmap_filename) :
root_hash_(root_hash), hashes_(NULL), peak_count_(0), fd_(0), hash_fd_(0),
filename_(filename), size_(0), sizec_(0), complete_(0), completec_(0),
chunk_size_(chunk_size)
{
    fd_ = open(filename,OPENFLAGS,S_IRUSR|S_IWUSR|S_IRGRP|S_IROTH);
    if (fd_<0) {
        fd_ = 0;
        print_error("cannot open the file");
        return;
    }
	std::string hfn;
	if (!hash_filename){
		hfn.assign(filename);
		hfn.append(".mhash");
		hash_filename = hfn.c_str();
	}

	std::string bfn;
	if (!binmap_filename){
		bfn.assign(filename);
		bfn.append(".mbinmap");
		binmap_filename = bfn.c_str();
	}

	// Arno: if user doesn't want to check hashes but no .mhash, check hashes anyway
	bool actually_check_hashes = check_hashes;
    bool mhash_exists=true;
#ifdef WIN32
    struct _stat buf;
#else
    struct stat buf;
#endif
    int res = stat( hash_filename, &buf );
    if( res < 0 && errno == ENOENT)
    	mhash_exists = false;
    if (!mhash_exists && !check_hashes)
    	actually_check_hashes = true;


    // Arno: if the remainder of the hashtree state is on disk we can
    // hashcheck very quickly
    bool binmap_exists=true;
    res = stat( binmap_filename, &buf );
    if( res < 0 && errno == ENOENT)
    	binmap_exists = false;

    fprintf(stderr,"hashtree: hashchecking want %s do %s binmap-on-disk %s\n", (check_hashes ? "yes" : "no"), (actually_check_hashes? "yes" : "no"), (binmap_exists? "yes" : "no") );

    hash_fd_ = open(hfn.c_str(),OPENFLAGS,S_IRUSR|S_IWUSR|S_IRGRP|S_IROTH);
    if (hash_fd_<0) {
        hash_fd_ = 0;
        print_error("cannot open hash file");
        return;
    }

    // Arno: if user wants to or no .mhash, and if root hash unknown (new file) and no checkpoint, (re)calc root hash
    if (file_size(fd_) && ((actually_check_hashes) || (root_hash_==Sha1Hash::ZERO && !binmap_exists) || (!mhash_exists)) ) {
    	// fresh submit, hash it
    	dprintf("%s hashtree full compute\n",tintstr());
        assert(file_size(fd_));
        Submit();
    } else if (mhash_exists && binmap_exists && file_size(hash_fd_)) {
    	// Arno: recreate hash tree without rereading content
    	dprintf("%s hashtree read from checkpoint\n",tintstr());
    	FILE *fp = fopen(binmap_filename,"rb");
    	if (!fp) {
    		 print_error("hashtree: cannot open .mbinmap file");
    		 return;
    	}
    	if (deserialize(fp) < 0) {
    		// Try to rebuild hashtree data
    		Submit();
    	}
    } else {
    	// Arno: no data on disk, or mhash on disk, but no binmap. In latter
    	// case recreate binmap by reading content again. Historic optimization
    	// of Submit.
    	dprintf("%s hashtree empty or partial recompute\n",tintstr());
        RecoverProgress();
    }
}

// Reads complete file and constructs hash tree
void            HashTree::Submit () {
    size_ = file_size(fd_);
    sizec_ = (size_ + chunk_size_-1) / chunk_size_;

    fprintf(stderr,"hashtree: submit: cs %i\n", chunk_size_);

    peak_count_ = gen_peaks(sizec_,peaks_);
    int hashes_size = Sha1Hash::SIZE*sizec_*2;
    dprintf("%s hashtree submit resizing hash file\n",tintstr() );
    file_resize(hash_fd_,hashes_size);
    hashes_ = (Sha1Hash*) memory_map(hash_fd_,hashes_size);
    if (!hashes_) {
        size_ = sizec_ = complete_ = completec_ = 0;
        print_error("mmap failed");
        return;
    }
    size_t last_piece_size = (sizec_ - 1) % (chunk_size_) + 1;
    char *chunk = new char[chunk_size_];
    for (uint64_t i=0; i<sizec_; i++) {

        size_t rd = read(fd_,chunk,chunk_size_);
        if (rd<(chunk_size_) && i!=sizec_-1) {
            free(hashes_);
            hashes_=NULL;
            return;
        }
        bin_t pos(0,i);
        hashes_[pos.toUInt()] = Sha1Hash(chunk,rd);
        ack_out_.set(pos);
        while (pos.is_right()){
            pos = pos.parent();
            hashes_[pos.toUInt()] = Sha1Hash(hashes_[pos.left().toUInt()],hashes_[pos.right().toUInt()]);
        }
        complete_+=rd;
        completec_++;
    }
    delete chunk;
    for (int p=0; p<peak_count_; p++) {
        peak_hashes_[p] = hashes_[peaks_[p].toUInt()];
    }

    root_hash_ = DeriveRoot();

}


/** Basically, simulated receiving every single chunk, except
 for some optimizations.
 Precondition: root hash known */
void            HashTree::RecoverProgress () {

	fprintf(stderr,"hashtree: recover: cs %i\n", chunk_size_);

	if (!RecoverPeakHashes())
		return;

    // at this point, we may use mmapd hashes already
    // so, lets verify hashes and the data we've got
    char *zero_chunk = new char[chunk_size_];
    memset(zero_chunk, 0, chunk_size_);
    Sha1Hash zero_hash(zero_chunk,chunk_size_);

    // Arno: loop over all pieces, read each from file
    // ARNOSMPTODO: problem is that we may have the complete hashtree, but
    // not have all pieces. So hash file gives too little information to
    // determine whether file is complete on disk.
    //
    char *buf = new char[chunk_size_];
    for(int p=0; p<size_in_chunks(); p++) {
        bin_t pos(0,p);
        if (hashes_[pos.toUInt()]==Sha1Hash::ZERO)
            continue;
        size_t rd = read(fd_,buf,chunk_size_);
        if (rd!=(chunk_size_) && p!=size_in_chunks()-1)
            break;
        if (rd==(chunk_size_) && !memcmp(buf, zero_chunk, rd) &&
                hashes_[pos.toUInt()]!=zero_hash) // FIXME // Arno == don't have piece yet?
            continue;
        if (!OfferHash(pos, Sha1Hash(buf,rd)) )
            continue;
        ack_out_.set(pos);
        completec_++;
        complete_+=rd;
        if (rd!=(chunk_size_) && p==size_in_chunks()-1) // set the exact file size
            size_ = ((sizec_-1)*chunk_size_) + rd;
    }
    delete buf;
    delete zero_chunk;
}

/** Precondition: root hash known */
bool HashTree::RecoverPeakHashes()
{
    uint64_t size = file_size(fd_);
    uint64_t sizek = (size + chunk_size_-1) / chunk_size_;

	// Arno: Calc location of peak hashes, read them from hash file and check if
	// they match to root hash. If so, load hashes into memory.
	bin_t peaks[64];
    int peak_count = gen_peaks(sizek,peaks);
    for(int i=0; i<peak_count; i++) {
        Sha1Hash peak_hash;
        file_seek(hash_fd_,peaks[i].toUInt()*sizeof(Sha1Hash));
        if (read(hash_fd_,&peak_hash,sizeof(Sha1Hash))!=sizeof(Sha1Hash))
            return false;
        OfferPeakHash(peaks[i], peak_hash);
    }
    if (!this->size())
        return false; // if no valid peak hashes found

    return true;
}

int HashTree::serialize(FILE *fp)
{
	fprintf_retiffail(fp,"version %i\n", 1 );
	fprintf_retiffail(fp,"root hash %s\n", root_hash_.hex().c_str() );
	fprintf_retiffail(fp,"chunk size %i\n", chunk_size_ );
	fprintf_retiffail(fp,"complete %lli\n", complete_ );
	fprintf_retiffail(fp,"completec %lli\n", completec_ );
	return ack_out_.serialize(fp);
}


/** Arno: recreate hash tree from .mbinmap file without rereading content.
 * Precondition: root hash known
 */
int HashTree::deserialize(FILE *fp) {

	char hexhashstr[256];
	uint64_t c,cc;
	size_t cs;
	int version;

	fscanf_retiffail(fp,"version %i\n", &version );
	fscanf_retiffail(fp,"root hash %s\n", hexhashstr);
	fscanf_retiffail(fp,"chunk size %i\n", &cs);
	fscanf_retiffail(fp,"complete %lli\n", &c );
	fscanf_retiffail(fp,"completec %lli\n", &cc );

	fprintf(stderr,"hashtree: deserialize: %s %lli ~ %lli * %i\n", hexhashstr, c, cc, cs );

	if (ack_out_.deserialize(fp) < 0)
		return -1;
	root_hash_ = Sha1Hash(true, hexhashstr);
	chunk_size_ = cs;

	if (!RecoverPeakHashes()) {
		root_hash_ = Sha1Hash::ZERO;
		ack_out_.clear();
		return -1;
	}

	// Are reset by RecoverPeakHashes() for some reason.
	complete_ = c;
	completec_ = cc;
    size_ = file_size(fd_);
    sizec_ = (size_ + chunk_size_-1) / chunk_size_;

    return 0;
}


bool            HashTree::OfferPeakHash (bin_t pos, const Sha1Hash& hash) {
	char bin_name_buf[32];
	dprintf("%s hashtree offer peak %s\n",tintstr(),pos.str(bin_name_buf));

    assert(!size_);
    if (peak_count_) {
        bin_t last_peak = peaks_[peak_count_-1];
        if ( pos.layer()>=last_peak.layer() ||
            pos.base_offset()!=last_peak.base_offset()+last_peak.base_length() )
            peak_count_ = 0;
    }
    peaks_[peak_count_] = pos;
    peak_hashes_[peak_count_] = hash;
    peak_count_++;
    // check whether peak hash candidates add up to the root hash
    Sha1Hash mustbe_root = DeriveRoot();
    if (mustbe_root!=root_hash_)
        return false;
    for(int i=0; i<peak_count_; i++)
        sizec_ += peaks_[i].base_length();

    // bingo, we now know the file size (rounded up to a chunk_size() unit)

    size_ = sizec_ * chunk_size_;
    completec_ = complete_ = 0;
    sizec_ = (size_ + chunk_size_-1) / chunk_size_;

    // ARNOTODO: win32: this is pretty slow for ~200 MB already. Perhaps do
    // on-demand sizing for Win32?
    uint64_t cur_size = file_size(fd_);
    if ( cur_size<=(sizec_-1)*chunk_size_  || cur_size>sizec_*chunk_size_ ) {
    	dprintf("%s hashtree offerpeak resizing file\n",tintstr() );
        if (file_resize(fd_, size_)) {
            print_error("cannot set file size\n");
            size_=0; // remain in the 0-state
            return false;
        }
    }

    // mmap the hash file into memory
    uint64_t expected_size = sizeof(Sha1Hash)*sizec_*2;
    // Arno, 2011-10-18: on Windows we could optimize this away,
    //CreateFileMapping, see compat.cpp will resize the file for us with
    // the right params.
    //
    if ( file_size(hash_fd_) != expected_size ) {
    	dprintf("%s hashtree offerpeak resizing hash file\n",tintstr() );
        file_resize (hash_fd_, expected_size);
    }

    hashes_ = (Sha1Hash*) memory_map(hash_fd_,expected_size);
    if (!hashes_) {
        size_ = sizec_ = complete_ = completec_ = 0;
        print_error("mmap failed");
        return false;
    }

    for(int i=0; i<peak_count_; i++)
        hashes_[peaks_[i].toUInt()] = peak_hashes_[i];

    dprintf("%s hashtree memory mapped\n",tintstr() );

    return true;
}


Sha1Hash        HashTree::DeriveRoot () {

	dprintf("%s hashtree deriving root\n",tintstr() );

    int c = peak_count_-1;
    bin_t p = peaks_[c];
    Sha1Hash hash = peak_hashes_[c];
    c--;
    // Arno, 2011-10-14: Root hash = top of smallest tree covering content IMHO.
    //while (!p.is_all()) {
    while (c >= 0) {
        if (p.is_left()) {
            p = p.parent();
            hash = Sha1Hash(hash,Sha1Hash::ZERO);
        } else {
            if (c<0 || peaks_[c]!=p.sibling())
                return Sha1Hash::ZERO;
            hash = Sha1Hash(peak_hashes_[c],hash);
            p = p.parent();
            c--;
        }
    }
    //fprintf(stderr,"root bin is %lli covers %lli\n", p.toUInt(), p.base_length() );
    return hash;
}


/** For live streaming: appends the data, adjusts the tree.
    @ return the number of fresh (tail) peak hashes */
int         HashTree::AppendData (char* data, int length) {
    return 0;
}


bin_t         HashTree::peak_for (bin_t pos) const {
    int pi=0;
    while (pi<peak_count_ && !peaks_[pi].contains(pos))
        pi++;
    return pi==peak_count_ ? bin_t(bin_t::NONE) : peaks_[pi];
}

bool            HashTree::OfferHash (bin_t pos, const Sha1Hash& hash) {
    if (!size_)  // only peak hashes are accepted at this point
        return OfferPeakHash(pos,hash);
    bin_t peak = peak_for(pos);
    if (peak.is_none())
        return false;
    if (peak==pos)
        return hash == hashes_[pos.toUInt()];
    if (!ack_out_.is_empty(pos.parent()))
        return hash==hashes_[pos.toUInt()]; // have this hash already, even accptd data
    // LESSHASH
    // Arno: if we already verified this hash against the root, don't replace
    if (!is_hash_verified_.is_empty(bin_t(0,pos.toUInt())))
    	return hash == hashes_[pos.toUInt()];

    hashes_[pos.toUInt()] = hash;
    if (!pos.is_base())
        return false; // who cares?
    bin_t p = pos;
    Sha1Hash uphash = hash;
    // Arno: Note well: bin_t(0,p.toUInt()) is to abuse binmap as bitmap.
    while ( p!=peak && ack_out_.is_empty(p) && is_hash_verified_.is_empty(bin_t(0,p.toUInt())) ) {
        hashes_[p.toUInt()] = uphash;
        p = p.parent();
		// Arno: Prevent poisoning the tree with bad values:
		// Left hand hashes should never be zero, and right
		// hand hash is only zero for the last packet, i.e.,
		// layer 0. Higher layers will never have 0 hashes
		// as SHA1(zero+zero) != zero (but b80de5...)
		//
        if (hashes_[p.left().toUInt()] == Sha1Hash::ZERO || hashes_[p.right().toUInt()] == Sha1Hash::ZERO)
        	break;
        uphash = Sha1Hash(hashes_[p.left().toUInt()],hashes_[p.right().toUInt()]);
    }// walk to the nearest proven hash

    bool success = (uphash==hashes_[p.toUInt()]);
    // LESSHASH
    if (success) {
    	// Arno: The hash checks out. Mark all hashes on the uncle path as
    	// being verified, so we don't have to go higher than them on a next
    	// check.
    	p = pos;
    	// Arno: Note well: bin_t(0,p.toUInt()) is to abuse binmap as bitmap.
    	is_hash_verified_.set(bin_t(0,p.toUInt()));
        while (p.layer() != peak.layer()) {
            p = p.parent().sibling();
        	is_hash_verified_.set(bin_t(0,p.toUInt()));
        }
        // Also mark hashes on direct path to root as verified. Doesn't decrease
        // #checks, but does increase the number of verified hashes faster.
    	p = pos;
        while (p != peak) {
            p = p.parent();
        	is_hash_verified_.set(bin_t(0,p.toUInt()));
        }
    }

    return success;
}


bool            HashTree::OfferData (bin_t pos, const char* data, size_t length) {
    if (!size())
        return false;
    if (!pos.is_base())
        return false;
    if (length<chunk_size_ && pos!=bin_t(0,sizec_-1))
        return false;
    if (ack_out_.is_filled(pos))
        return true; // to set data_in_
    bin_t peak = peak_for(pos);
    if (peak.is_none())
        return false;

    Sha1Hash data_hash(data,length);
    if (!OfferHash(pos, data_hash)) {
        char bin_name_buf[32];
//        printf("invalid hash for %s: %s\n",pos.str(bin_name_buf),data_hash.hex().c_str()); // paranoid
    	//fprintf(stderr,"INVALID HASH FOR %lli layer %d\n", pos.toUInt(), pos.layer() );
    	dprintf("%s hashtree check failed (bug TODO) %s\n",tintstr(),pos.str(bin_name_buf));
        return false;
    }

    //printf("g %lli %s\n",(uint64_t)pos,hash.hex().c_str());
    ack_out_.set(pos);
    // Arno,2011-10-03: appease g++
    if (pwrite(fd_,data,length,pos.base_offset()*chunk_size_) < 0)
    	print_error("pwrite failed");
    complete_ += length;
    completec_++;
    if (pos.base_offset()==sizec_-1) {
        size_ = ((sizec_-1)*chunk_size_) + length;
        if (file_size(fd_)!=size_)
            file_resize(fd_,size_);
    }
    return true;
}


uint64_t      HashTree::seq_complete () {
    uint64_t seqc = ack_out_.find_empty().base_offset();
    if (seqc==sizec_)
        return size_;
    else
        return seqc*chunk_size_;
}

HashTree::~HashTree () {
    if (hashes_)
        memory_unmap(hash_fd_, hashes_, sizec_*2*sizeof(Sha1Hash));
    if (fd_)
        close(fd_);
    if (hash_fd_)
        close(hash_fd_);
}

