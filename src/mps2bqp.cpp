#define _FILE_OFFSET_BITS 64
#include "internal.h"
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
#include <stdexcept>
#include <string>
#include <string_view>
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
class Writer {
    FILE *f=nullptr; std::string temp,output; bool finished=false;
    std::array<Bytes,BQP_ARRAYS+1> pending;
    std::array<uint64_t,BQP_ARRAYS+1> counts{};
    int level;
    uint64_t block_count=0;
    std::array<uint64_t,8> enc_counts{};
public:
    bqp_info info{};
    std::array<uint32_t,BQP_ARRAYS+1> crc{};
    Writer(const char *path,unsigned block,int zlevel):output(path),level(zlevel) {
        require(bqp_platform(),"requires little-endian IEEE binary64 host");
        require(!std::filesystem::exists(output),"output already exists: "+output);
        require(level==0||bqp_zstd_available(),"libzstd.so.1 unavailable (use --zstd 0 for no compression)");
        temp=output+".partial."+std::to_string(getpid()); f=fopen(temp.c_str(),"wbx+");
        require(f,"cannot create output: "+temp); setvbuf(f,nullptr,_IOFBF,4*1024*1024);
        unsigned char zero[128]={0}; put(zero,128);
        info.sense=1; info.a_index_bytes=info.q_index_bytes=8; info.block_elements=block;
    }
    ~Writer() { if(f) fclose(f); if(!finished&&!temp.empty()) unlink(temp.c_str()); }
    void put(const void *p,size_t n) { require(fwrite(p,1,n,f)==n,"output write failed (check free disk space)"); }
    void flush(unsigned a) {
        Bytes &raw=pending[a]; if(raw.empty()) return;
        unsigned w=bqp_element_bytes(&info,a); uint64_t n=raw.size()/w;
        bool integer=a==BQP_AP||a==BQP_AI||a==BQP_QP||a==BQP_QI;
        Encoded e=integer?encode_integer(raw.data(),(size_t)n,w):encode_real((const double*)raw.data(),(size_t)n);
        Bytes compressed; unsigned comp=0; const Bytes *payload=&e.data;
        if(level!=0) {
            compressed.resize(bqp_zstd_bound(e.data.size()));
            size_t sz=bqp_zstd_compress(compressed.data(),compressed.size(),e.data.data(),e.data.size(),level);
            require(!bqp_zstd_error(sz),"Zstd compression failed");
            if(sz<e.data.size()) { compressed.resize(sz); comp=1; payload=&compressed; }
        }
        unsigned char bh[64]={0}; bqp_p32(bh,0x314b4c42); bqp_p32(bh+4,a);
        bqp_p64(bh+8,counts[a]); bqp_p64(bh+16,n); bqp_p32(bh+24,e.code); bqp_p32(bh+28,comp);
        bqp_p64(bh+32,e.data.size()); bqp_p64(bh+40,payload->size());
        bqp_p32(bh+48,(uint32_t)crc32(0,raw.data(),(uInt)raw.size()));
        bqp_p32(bh+52,e.dict); bqp_p32(bh+56,e.width); bqp_p32(bh+60,(uint32_t)crc32(0,bh,60));
        put(bh,64); put(payload->data(),payload->size());
        unsigned char zero[8]={0}; put(zero,(8-payload->size()%8)%8);
        crc[a]=(uint32_t)crc32(crc[a],raw.data(),(uInt)raw.size());
        counts[a]+=n; ++block_count; ++enc_counts[e.code]; raw.clear();
    }
    void append(unsigned a,const void *p,size_t n) {
        unsigned w=bqp_element_bytes(&info,a); const unsigned char *s=(const unsigned char*)p;
        while(n) {
            Bytes &v=pending[a]; size_t take=std::min(n,(size_t)info.block_elements-v.size()/w);
            v.insert(v.end(),s,s+take*w); s+=take*w; n-=take;
            if(v.size()/w==info.block_elements) flush(a);
        }
    }
    void real(unsigned a,double v) { append(a,&v,1); }
    void integer(unsigned a,uint64_t v) {
        if(bqp_element_bytes(&info,a)==4) { require(v<=UINT32_MAX,"index overflow"); uint32_t x=(uint32_t)v; append(a,&x,1); }
        else append(a,&v,1);
    }
    void finish() {
        for(unsigned a=1;a<=BQP_ARRAYS;a++) {
            flush(a); require(counts[a]==bqp_array_count(&info,a),"writer array length mismatch");
        }
        auto size=ftello(f); require(size>=128,"ftello failed"); info.file_bytes=(uint64_t)size; info.blocks=block_count;
        unsigned char h[128]={0}; memcpy(h,"BQP1\r\n\032\n",8); bqp_p32(h+8,1); bqp_p32(h+12,128);
        bqp_p64(h+16,info.rows); bqp_p64(h+24,info.cols); bqp_p64(h+32,info.nnz); bqp_p64(h+40,info.qnnz);
        bqp_p64(h+48,block_count); bqp_p64(h+56,info.file_bytes); bqp_p32(h+64,(uint32_t)info.sense);
        bqp_p32(h+68,info.a_index_bytes); bqp_p32(h+72,info.q_index_bytes); bqp_p32(h+76,1);
        memcpy(h+80,&info.objective_offset,8); bqp_p64(h+88,info.block_elements); bqp_p32(h+124,(uint32_t)crc32(0,h,124));
        require(fseeko(f,0,SEEK_SET)==0,"seek failed"); put(h,128);
        require(fflush(f)==0&&fsync(fileno(f))==0,"output flush failed");
        require(fclose(f)==0,"output close failed"); f=nullptr;
        /* Publish without replacing an existing file. */
        require(link(temp.c_str(),output.c_str())==0,"cannot publish output (already exists or link failed)");
        unlink(temp.c_str()); finished=true;
    }
    void print_stats(double seconds,uint64_t input_bytes,uint64_t input_entries) {
        uint64_t memory=0; bqp_memory_bytes(&info,&memory); struct rusage ru{}; getrusage(RUSAGE_SELF,&ru);
        std::cout<<"{\"rows\":"<<info.rows<<",\"cols\":"<<info.cols<<",\"nnz\":"<<info.nnz
            <<",\"qnnz\":"<<info.qnnz<<",\"input_entries\":"<<input_entries
            <<",\"input_bytes\":"<<input_bytes<<",\"file_bytes\":"<<info.file_bytes
            <<",\"decoded_bytes\":"<<memory<<",\"seconds\":"<<seconds<<",\"max_rss_kib\":"<<ru.ru_maxrss
            <<",\"blocks\":"<<info.blocks<<",\"encoding_blocks\":[";
        for(unsigned i=0;i<8;i++) std::cout<<(i?",":"")<<enc_counts[i];
        std::cout<<"],\"array_crc32\":[";
        for(unsigned a=1;a<=BQP_ARRAYS;a++) std::cout<<(a>1?",":"")<<crc[a];
        std::cout<<"]}\n";
    }
};
class Parser {
    Input &in; Writer &w; Names rows,cols;
    std::vector<char> rowtype,lower_set;
    std::vector<double> rhs,ranges,c,lo,hi;
    std::vector<std::pair<uint64_t,double>> entries;
    std::map<std::pair<uint64_t,uint64_t>,double> quadratic;
    std::string section,obj,obj_requested,current_col,last_rhs,last_range,last_bound,qsection;
    bool seen_rows=false,seen_columns=false,ended=false;
    uint64_t nnz=0,input_nnz=0;
    Clock::time_point report=Clock::now();
    void flush_column() {
        if(current_col.empty()) return;
        bool sorted=true; for(size_t i=1;i<entries.size();i++) if(entries[i-1].first>entries[i].first) sorted=false;
        if(!sorted) std::stable_sort(entries.begin(),entries.end(),[](auto a,auto b){return a.first<b.first;});
        for(size_t i=0;i<entries.size();) {
            uint64_t r=entries[i].first; double value=entries[i++].second;
            while(i<entries.size()&&entries[i].first==r) { value+=entries[i++].second; require(std::isfinite(value),"duplicate coefficient overflow"); }
            w.integer(BQP_AI,r); w.real(BQP_AX,value); ++nnz;
        }
        w.integer(BQP_AP,nnz); entries.clear(); current_col.clear();
    }
    void transition(const std::string &next,const std::array<char*,12> &p,unsigned n) {
        if(section=="COLUMNS") flush_column();
        if(next=="ROWS") { require(!seen_rows,"repeated ROWS"); seen_rows=true; }
        if(next=="COLUMNS") {
            require(seen_rows&&!seen_columns&&!obj.empty(),"invalid ROWS/COLUMNS/objective order"); seen_columns=true;
            w.info.rows=rows.size(); w.info.a_index_bytes=rows.size()<=UINT32_MAX?4:8;
            w.integer(BQP_AP,0);
        }
        if(next=="QUADOBJ"||next=="QMATRIX") { require(qsection.empty(),"multiple quadratic sections"); qsection=next; }
        if(next=="ENDATA") ended=true;
        section=next;
        if((next=="OBJSENSE"||next=="OBJNAME")&&n==2) metadata(p[1]);
        else require(n==1||next=="NAME", "unexpected fields on section header");
    }
    void metadata(const char *value) {
        if(section=="OBJSENSE") {
            std::string s=value; for(char &x:s) if(x>='a'&&x<='z') x-=32;
            require(s=="MIN"||s=="MAX"||s=="MINIMIZE"||s=="MAXIMIZE","bad OBJSENSE");
            w.info.sense=s[0]=='M'&&s[1]=='A'?-1:1;
        } else { require(!seen_rows,"OBJNAME must precede ROWS"); obj_requested=value; }
    }
    void select_set(std::string &chosen,const char *name) {
        if(chosen.empty()) chosen=name;
        require(chosen==name,"multiple RHS/RANGES/BOUNDS sets are ambiguous; select one before converting");
    }
public:
    Parser(Input &i,Writer &o):in(i),w(o) {}
    uint64_t run() {
        char *line;
        while((line=in.next())) {
            if(*line=='*') continue;
            bool header_line=*line!=' '&&*line!='\t'&&*line!='\r'&&*line!=0;
            std::array<char*,12> p{}; unsigned n=tokenize(line,p); if(!n) continue;
            require(!ended,"unexpected data after ENDATA");
            if(header_line) {
                std::string s=p[0];
                require(s=="NAME"||s=="OBJSENSE"||s=="OBJNAME"||s=="ROWS"||s=="COLUMNS"||s=="RHS"||s=="RANGES"||s=="BOUNDS"||s=="QUADOBJ"||s=="QMATRIX"||s=="ENDATA",
                        "unsupported section or unindented data: "+s);
                transition(s,p,n); continue;
            }
            if(section=="OBJSENSE"||section=="OBJNAME") { require(n==1,"bad objective metadata"); metadata(p[0]); }
            else if(section=="ROWS") {
                require(n==2&&strlen(p[0])==1&&strchr("NLEG",p[0][0]),"bad ROWS record");
                char type=p[0][0];
                if(type=='N'&&((obj_requested.empty()&&obj.empty())||obj_requested==p[1])) { require(obj.empty(),"duplicate objective"); obj=p[1]; }
                else {
                    require(obj!=p[1],"duplicate objective row name"); rows.add(p[1]); rowtype.push_back(type);
                    rhs.push_back(0); ranges.push_back(std::numeric_limits<double>::quiet_NaN());
                }
            } else if(section=="COLUMNS") {
                require(n>=2,"bad COLUMNS record");
                for(unsigned k=0;k<n;k++) require(strcmp(p[k],"'MARKER'")!=0,"integer markers unsupported: continuous LP/QP only");
                unsigned begin;
                if(n==3||n==5) {
                    if(current_col!=p[0]) {
                        flush_column(); cols.add(p[0]); current_col=p[0]; c.push_back(0); lo.push_back(0); hi.push_back(INFINITY); lower_set.push_back(0);
                    }
                    begin=1;
                } else { require((n==2||n==4)&&!current_col.empty(),"bad blank-column continuation"); begin=0; }
                for(unsigned k=begin;k<n;k+=2) {
                    double v=number(p[k+1]);
                    if(obj==p[k]) { c.back()+=v; require(std::isfinite(c.back()),"objective overflow"); }
                    else { entries.emplace_back(rows.find(p[k]),v); ++input_nnz; }
                }
            } else if(section=="RHS"||section=="RANGES") {
                require(seen_columns,"RHS/RANGES before COLUMNS");
                unsigned begin=0;
                if(n==3||n==5) { select_set(section=="RHS"?last_rhs:last_range,p[0]); begin=1; }
                else require(n==2||n==4,"bad RHS/RANGES record");
                for(unsigned k=begin;k<n;k+=2) {
                    double v=number(p[k+1]);
                    if(obj==p[k]) { require(section=="RHS","range on objective unsupported"); w.info.objective_offset=-v; }
                    else {
                        uint64_t r=rows.find(p[k]);
                        if(section=="RHS") rhs[r]=v; else ranges[r]=v;
                    }
                }
            } else if(section=="BOUNDS") {
                require(n==3||n==4,"BOUNDS requires type, set, variable, optional value");
                select_set(last_bound,p[1]); uint64_t j=cols.find(p[2]); std::string type=p[0];
                bool needs=type=="LO"||type=="UP"||type=="FX";
                require((needs&&n==4)||(!needs&&n==3),"wrong BOUNDS field count");
                double v=needs?number(p[3]):0;
                if(type=="LO") { lo[j]=v; lower_set[j]=1; }
                else if(type=="UP") { hi[j]=v; if(v<0&&!lower_set[j]) lo[j]=-INFINITY; }
                else if(type=="FX") { lo[j]=hi[j]=v; lower_set[j]=1; }
                else if(type=="FR") { lo[j]=-INFINITY; hi[j]=INFINITY; lower_set[j]=1; }
                else if(type=="MI") { lo[j]=-INFINITY; lower_set[j]=1; }
                else if(type=="PL") hi[j]=INFINITY;
                else throw std::runtime_error("unsupported bound type (continuous LP/QP only): "+type);
            } else if(section=="QUADOBJ"||section=="QMATRIX") {
                require(n==3,"bad quadratic record"); uint64_t a=cols.find(p[0]),b=cols.find(p[1]);
                auto key=std::make_pair(a,b); double v=number(p[2]);
                auto it=quadratic.find(key);
                if(it==quadratic.end()) quadratic.emplace(key,v);
                else { it->second+=v; require(std::isfinite(it->second),"quadratic duplicate overflow"); }
            } else throw std::runtime_error("data in unsupported section: "+section);
            if((in.line&65535)==0&&std::chrono::duration<double>(Clock::now()-report).count()>10) {
                std::cerr<<"progress line="<<in.line<<" compressed_read="<<in.compressed_position()<<" cols="<<cols.size()<<" nnz="<<nnz<<"\n"; report=Clock::now();
            }
        }
        require(ended&&seen_columns,"missing ENDATA/COLUMNS (truncated input)");
        require(!obj.empty()&&(obj_requested.empty()||obj_requested==obj),"objective row not found");
        w.info.cols=cols.size(); w.info.nnz=nnz; w.info.q_index_bytes=cols.size()<=UINT32_MAX?4:8;
        for(size_t r=0;r<rowtype.size();r++) {
            double low=-INFINITY,up=INFINITY,b=rhs[r],range=ranges[r]; char t=rowtype[r];
            if(t=='E') low=up=b; else if(t=='L') up=b; else if(t=='G') low=b;
            if(!std::isnan(range)) {
                require(t!='N',"RANGES on free row unsupported");
                if(t=='L') low=b-std::fabs(range);
                else if(t=='G') up=b+std::fabs(range);
                else if(range>=0) up=b+range; else low=b+range;
                require((std::isfinite(low)||low==-INFINITY)&&(std::isfinite(up)||up==INFINITY),"range arithmetic overflow");
                require(!(std::isinf(low)&&t!='G')&&!(std::isinf(up)&&t!='L'),"range bound overflow");
            }
            w.real(BQP_RL,low); w.real(BQP_RU,up);
        }
        w.append(BQP_C,c.data(),c.size()); w.append(BQP_XL,lo.data(),lo.size()); w.append(BQP_XU,hi.data(),hi.size());
        /* Canonicalize Hessian to upper triangle, without doubling cross terms. */
        std::map<std::pair<uint64_t,uint64_t>,double> upper;
        int triangle=0;
        for(const auto &item:quadratic) {
            uint64_t a=item.first.first,b=item.first.second;
            if(qsection=="QMATRIX"&&a!=b) {
                auto other=quadratic.find({b,a});
                require(other!=quadratic.end()&&other->second==item.second,"QMATRIX must provide matching symmetric entries");
                if(a>b) continue;
            } else if(qsection=="QUADOBJ"&&a!=b) {
                int side=a<b?1:-1; require(!triangle||side==triangle,"QUADOBJ must contain a single triangle"); triangle=side;
            }
            uint64_t row=std::min(a,b),col=std::max(a,b);
            require(upper.emplace(std::make_pair(col,row),item.second).second,"duplicate mirrored quadratic entry");
        }
        w.info.qnnz=upper.size();
        if(!upper.empty()) {
            uint64_t j=0,k=0; w.integer(BQP_QP,0);
            for(const auto &item:upper) {
                while(j<item.first.first) { w.integer(BQP_QP,k); ++j; }
                w.integer(BQP_QI,item.first.second); w.real(BQP_QX,item.second); ++k;
            }
            while(j<cols.size()) { w.integer(BQP_QP,k); ++j; }
        }
        return input_nnz;
    }
};
int main(int argc,char **argv) {
    if(argc<3) { std::cerr<<"usage: mps2bqp INPUT.mps[.gz] OUTPUT.bqp [--block 65536] [--zstd 1]\n"; return 2; }
    unsigned block=65536; int level=1;
    try {
        for(int i=3;i<argc;i+=2) {
            require(i+1<argc,"missing option value");
            if(std::string(argv[i])=="--block") { int b=std::stoi(argv[i+1]); require(b>=1&&(unsigned)b<=BQP_MAX_ELEMENTS,"block must be 1..1048576"); block=(unsigned)b; }
            else if(std::string(argv[i])=="--zstd") { level=std::stoi(argv[i+1]); require(level>=-5&&level<=19,"zstd level must be -5..19; 0 disables"); }
            else throw std::runtime_error("unknown option");
        }
        auto start=Clock::now(); Input input(argv[1]); Writer writer(argv[2],block,level);
        try {
            Parser parser(input,writer); uint64_t entries=parser.run(); writer.finish();
            writer.print_stats(std::chrono::duration<double>(Clock::now()-start).count(),std::filesystem::file_size(argv[1]),entries);
        } catch(const std::exception &e) { throw std::runtime_error("line "+std::to_string(input.line)+": "+e.what()); }
    } catch(const std::exception &e) { std::cerr<<"mps2bqp: "<<e.what()<<"\n"; return 1; }
    return 0;
}
