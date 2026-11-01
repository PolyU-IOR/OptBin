#pragma once
#include "internal.h"
#include "optbin.h"
#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <unordered_map>
#include <vector>
#include <sys/resource.h>
#include <unistd.h>
#include <zlib.h>
using Bytes=std::vector<unsigned char>;
using Clock=std::chrono::steady_clock;
static void require(bool ok,const std::string &why) { if(!ok) throw std::runtime_error(why); }
static void require(bool ok,const char *why) { if(!ok) throw std::runtime_error(why); }
static uint64_t bits(double x) { uint64_t b; memcpy(&b,&x,8); return b; }
static double number(const char *s) {
    std::string converted;
    if(strchr(s,'D')||strchr(s,'d')) { converted=s; for(char &c:converted) if(c=='D'||c=='d') c='E'; s=converted.c_str(); }
    if(*s=='+') ++s;
    double v=0; auto r=std::from_chars(s,s+strlen(s),v,std::chars_format::general);
    if(r.ec!=std::errc()||*r.ptr!='\0'||!std::isfinite(v))
        throw std::runtime_error(std::string("invalid/nonfinite number: ")+s);
    return v;
}
static unsigned tokenize(char *line,std::array<char*,12> &p) {
    unsigned n=0; char *s=line;
    while(*s) {
        while(*s==' '||*s=='\t'||*s=='\r') ++s;
        if(!*s||*s=='$') break;
        require(n<p.size(),"too many MPS fields"); p[n++]=s;
        while(*s&&*s!=' '&&*s!='\t'&&*s!='\r') ++s;
        if(*s) *s++=0;
    }
    return n;
}
/* Consecutive names (R1,R2,... / C1,C2,...) use O(1) lookup memory.
 * Arbitrary names transparently switch to a hash table. */
class Names {
    bool sequential=true;
    uint64_t count_=0,base=0;
    std::string prefix;
    std::unordered_map<std::string,uint64_t> map;
    static bool split(std::string_view s,std::string_view &pre,uint64_t &v) {
        size_t k=s.size(); while(k&&s[k-1]>='0'&&s[k-1]<='9') --k;
        if(k==s.size()||(s.size()-k>1&&s[k]=='0')) return false;
        pre=s.substr(0,k); auto r=std::from_chars(s.data()+k,s.data()+s.size(),v);
        return r.ec==std::errc()&&v<=INT64_MAX;
    }
    void materialize() {
        for(uint64_t i=0;i<count_;i++) map.emplace(prefix+std::to_string(base+i),i);
        sequential=false;
    }
public:
    uint64_t size() const { return count_; }
    uint64_t add(std::string_view name) {
        std::string_view pre; uint64_t v=0;
        if(sequential) {
            bool ok=split(name,pre,v);
            if(!count_&&ok) { prefix=pre; base=v; }
            if(ok&&pre==prefix&&v==base+count_) return count_++;
            materialize();
        }
        auto r=map.emplace(std::string(name),count_);
        require(r.second,"duplicate/noncontiguous name: "+std::string(name));
        return count_++;
    }
    uint64_t find(std::string_view name) const {
        if(sequential) {
            std::string_view pre; uint64_t v;
            if(split(name,pre,v)&&pre==prefix&&v>=base&&v-base<count_) return v-base;
        } else { auto it=map.find(std::string(name)); if(it!=map.end()) return it->second; }
        throw std::runtime_error("unknown name: "+std::string(name));
    }
};
class Input {
    gzFile f=nullptr; std::vector<char> buf; size_t pos=0,end=0;
    std::string tail;
public:
    uint64_t line=0,raw_bytes=0;
    explicit Input(const char *path):buf(4*1024*1024+1) {
        f=gzopen(path,"rb"); require(f,"cannot open input"); gzbuffer(f,1024*1024);
    }
    ~Input() { if(f) gzclose(f); }
    uint64_t compressed_position() const { auto n=gzoffset(f); return n<0?0:(uint64_t)n; }
    char *next() {
        tail.clear();
        for(;;) {
            if(pos==end) {
                int n=gzread(f,buf.data(),(unsigned)buf.size()-1);
                if(n<=0) {
                    int code=0; const char *err=gzerror(f,&code);
                    require(n==0&&(code==Z_OK||code==Z_STREAM_END),std::string("input decompression: ")+err);
                    if(tail.empty()) return nullptr;
                    ++line; return tail.data();
                }
                pos=0; end=(size_t)n; buf[end]=0;
            }
            char *p=buf.data()+pos;
            char *nl=(char*)memchr(p,'\n',end-pos);
            if(nl) {
                size_t n=(size_t)(nl-p); pos+=n+1; raw_bytes+=n+1; *nl=0; ++line;
                if(tail.empty()) return p;
                tail.append(p,n); return tail.data();
            }
            tail.append(p,end-pos); raw_bytes+=end-pos; pos=end;
            require(tail.size()<1024*1024,"MPS line longer than 1 MiB");
        }
    }
};
struct Encoded { Bytes data; unsigned code=BQP_RAW,dict=0,width=0; };
static Encoded encode_real(const double *v,size_t n) {
    Encoded e; e.data.resize(n*8); memcpy(e.data.data(),v,n*8);
    bool i8=true,i16=true,i32=true,f32=true;
    std::unordered_map<uint64_t,unsigned> ids; ids.reserve(512);
    std::vector<uint64_t> dictionary; std::vector<unsigned> codes; codes.reserve(n);
    bool dict_ok=true;
    for(size_t i=0;i<n;i++) {
        double x=v[i]; uint64_t b=bits(x);
        if(i8||i16||i32) {
            bool integer=std::isfinite(x)&&std::trunc(x)==x&&!(x==0&&std::signbit(x));
            i8 &= integer&&x>=-128&&x<=127;
            i16 &= integer&&x>=-32768&&x<=32767;
            i32 &= integer&&x>=INT32_MIN&&x<=INT32_MAX;
        }
        if(f32) {
            if(std::isfinite(x)&&std::fabs(x)>std::numeric_limits<float>::max()) f32=false;
            else f32=bits((double)(float)x)==b;
        }
        if(dict_ok) {
            auto it=ids.find(b);
            if(it==ids.end()) {
                if(dictionary.size()==4096) { dict_ok=false; ids.clear(); codes.clear(); }
                else { unsigned id=(unsigned)dictionary.size(); dictionary.push_back(b); ids.emplace(b,id); codes.push_back(id); }
            } else codes.push_back(it->second);
        }
    }
    unsigned w=i8?1:i16?2:(i32||f32)?4:8;
    if(w<8) {
        e.code=i8?BQP_I8:i16?BQP_I16:i32?BQP_I32:BQP_F32; e.data.resize(n*w);
        for(size_t i=0;i<n;i++) {
            if(e.code==BQP_I8) { int8_t x=(int8_t)v[i]; memcpy(e.data.data()+i,&x,1); }
            else if(e.code==BQP_I16) { int16_t x=(int16_t)v[i]; memcpy(e.data.data()+2*i,&x,2); }
            else if(e.code==BQP_I32) { int32_t x=(int32_t)v[i]; memcpy(e.data.data()+4*i,&x,4); }
            else { float x=(float)v[i]; memcpy(e.data.data()+4*i,&x,4); }
        }
    }
    if(dict_ok&&dictionary.size()==1) {
        if(e.data.size()>=8) {
            e.code=BQP_CONST; e.data.resize(8); memcpy(e.data.data(),dictionary.data(),8);
        }
    } else if(dict_ok) {
        unsigned width=0; while((1u<<width)<dictionary.size()) ++width;
        size_t size=dictionary.size()*8+(n*width+7)/8;
        if(size<e.data.size()) {
            e.code=BQP_DICT; e.dict=(unsigned)dictionary.size(); e.width=width;
            e.data.assign(size,0); memcpy(e.data.data(),dictionary.data(),dictionary.size()*8);
            unsigned char *p=e.data.data()+dictionary.size()*8;
            for(size_t i=0;i<n;i++) {
                size_t bit=i*width; unsigned shift=bit%8, x=codes[i]<<shift;
                for(unsigned k=0;k<(width+shift+7)/8;k++) p[bit/8+k]|=(unsigned char)(x>>(8*k));
            }
        }
    }
    return e;
}
static Encoded encode_integer(const unsigned char *p,size_t n,unsigned w) {
    Encoded e; e.data.assign(p,p+n*w); Bytes delta(8);
    uint64_t prev=w==4?bqp_u32(p):bqp_u64(p); bqp_p64(delta.data(),prev);
    for(size_t i=1;i<n;i++) {
        uint64_t v=w==4?bqp_u32(p+i*w):bqp_u64(p+i*w);
        uint64_t diff=v>=prev?v-prev:prev-v;
        require(diff<=INT64_MAX,"index delta exceeds signed 64-bit range");
        uint64_t z=(diff<<1)-(v<prev?1:0);
        while(z>=128) { delta.push_back((unsigned char)((z&127)|128)); z>>=7; }
        delta.push_back((unsigned char)z); prev=v;
    }
    if(delta.size()<e.data.size()) { e.data=std::move(delta); e.code=BQP_DELTA; }
    return e;
}
