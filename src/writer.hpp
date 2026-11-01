#pragma once
#include "converter_common.hpp"
#include <memory>

/* One compressed spool per section permits MPS column streaming while the
 * eventual section lengths and complete model metadata are still unknown. */
class OptWriter {
    struct Section {
        FILE *spool=nullptr;
        uint32_t type;
        uint64_t count=0,blocks=0,bytes=0;
        Bytes pending;
        explicit Section(unsigned t):type(t) { spool=tmpfile(); require(spool,"cannot create section spool"); }
        ~Section() { if(spool) fclose(spool); }
    };
    std::map<unsigned,std::unique_ptr<Section>> sections;
    int level;
    unsigned encoding;
    std::string output;
    static void put(FILE *f,const void *p,size_t n) { require(fwrite(p,1,n,f)==n,"write failed"); }
    void flush(unsigned id,Section &s) {
        if(s.pending.empty()) return;
        unsigned width=optb_type_bytes(s.type);
        uint64_t n=s.pending.size()/width;
        Encoded enc;
        if(s.type==OPTB_F64&&(encoding&1)) {
            std::vector<double> aligned((size_t)n);
            memcpy(aligned.data(),s.pending.data(),s.pending.size());
            enc=encode_real(aligned.data(),(size_t)n);
        } else if(s.type==OPTB_U8||s.type==OPTB_F64||!(encoding&2)) enc.data=s.pending;
        else enc=encode_integer(s.pending.data(),(size_t)n,width);
        Bytes compressed; const Bytes *payload=&enc.data; unsigned comp=0;
        if(level) {
            compressed.resize(bqp_zstd_bound(enc.data.size()));
            size_t z=bqp_zstd_compress(compressed.data(),compressed.size(),enc.data.data(),enc.data.size(),level);
            require(!bqp_zstd_error(z),"zstd compression failed"); compressed.resize(z);
            if(z<enc.data.size()) { payload=&compressed; comp=1; }
        }
        unsigned char h[64]={0}; memcpy(h,"BLK1",4); bqp_p32(h+4,id);
        bqp_p64(h+8,s.count-n); bqp_p64(h+16,n); bqp_p32(h+24,enc.code); bqp_p32(h+28,comp);
        bqp_p64(h+32,enc.data.size()); bqp_p64(h+40,payload->size());
        bqp_p32(h+48,(uint32_t)crc32(0,s.pending.data(),(uInt)s.pending.size()));
        bqp_p32(h+52,enc.dict); bqp_p32(h+56,enc.width); bqp_p32(h+60,(uint32_t)crc32(0,h,60));
        put(s.spool,h,64); put(s.spool,payload->data(),payload->size());
        unsigned pad=(unsigned)(-payload->size()%8); unsigned char zero[8]={0}; put(s.spool,zero,pad);
        s.bytes+=64+payload->size()+pad; ++s.blocks; s.pending.clear();
    }
public:
    optb_info info{};
    OptWriter(const char *path,unsigned profile,unsigned block,int z,unsigned enc=3):level(z),encoding(enc),output(path) {
        require(bqp_platform(),"requires little-endian IEEE host");
        require(!std::filesystem::exists(output),"output already exists");
        require(block&&block<=BQP_MAX_ELEMENTS,"invalid block size");
        require(!level||bqp_zstd_available(),"libzstd.so.1 required (or use --zstd 0)");
        info.profile=profile; info.sense=1; info.block_elements=block;
    }
    void declare(unsigned id,unsigned type) {
        auto it=sections.find(id);
        if(it==sections.end()) sections.emplace(id,std::make_unique<Section>(type));
        else require(it->second->type==type,"section type changed");
    }
    void append(unsigned id,unsigned type,const void *p,size_t n) {
        declare(id,type); auto &s=*sections.at(id); unsigned width=optb_type_bytes(type);
        require(n<=INT64_MAX-s.count,"section count overflow");
        const unsigned char *q=(const unsigned char *)p;
        while(n) {
            size_t take=std::min(n,(size_t)info.block_elements-s.pending.size()/width);
            s.pending.insert(s.pending.end(),q,q+take*width); q+=take*width; n-=take; s.count+=take;
            if(s.pending.size()/width==info.block_elements) flush(id,s);
        }
    }
    void integer(unsigned id,uint64_t v) {
        unsigned type=sections.count(id)?sections.at(id)->type:(unsigned)OPTB_U64;
        require(type==OPTB_U32||type==OPTB_U64,"integer type required");
        if(type==OPTB_U32) { require(v<=UINT32_MAX,"32-bit index overflow"); uint32_t x=(uint32_t)v; append(id,type,&x,1); }
        else append(id,type,&v,1);
    }
    void real(unsigned id,double v) { append(id,OPTB_F64,&v,1); }
    template<class T> void vector(unsigned id,unsigned type,const std::vector<T> &v) {
        require(sizeof(T)==optb_type_bytes(type),"vector type mismatch"); append(id,type,v.data(),v.size());
    }
    void source(const std::string &s) { append(OPTB_SOURCE,OPTB_U8,s.data(),s.size()); }
    uint64_t count(unsigned id) const { auto i=sections.find(id); return i==sections.end()?0:i->second->count; }
    void finish() {
        info.sections=sections.size(); info.blocks=0; info.file_bytes=128+64*info.sections;
        for(auto &p:sections) { flush(p.first,*p.second); info.blocks+=p.second->blocks; info.file_bytes+=p.second->bytes; }
        unsigned char h[128]={0}; memcpy(h,"OPTBIN2\n",8); bqp_p32(h+8,2); bqp_p32(h+12,128);
        bqp_p64(h+16,info.sections); bqp_p64(h+24,info.blocks); bqp_p64(h+32,info.file_bytes);
        bqp_p64(h+40,128); bqp_p64(h+48,64*info.sections); bqp_p32(h+56,info.profile);
        bqp_p32(h+60,(uint32_t)info.sense); bqp_p64(h+64,info.cols); bqp_p64(h+72,info.rows);
        memcpy(h+80,&info.objective_offset,8); bqp_p64(h+88,info.block_elements); bqp_p32(h+124,(uint32_t)crc32(0,h,124));
        std::string temp=output+".partial.XXXXXX"; std::vector<char> name(temp.begin(),temp.end()); name.push_back(0);
        int fd=mkstemp(name.data()); require(fd>=0,"cannot create output temporary");
        FILE *f=fdopen(fd,"wb");
        if(!f) { close(fd); unlink(name.data()); throw std::runtime_error("cannot open output temporary"); }
        try {
            put(f,h,128); uint64_t offset=128+64*info.sections;
            for(const auto &p:sections) {
                const auto &s=*p.second; unsigned char d[64]={0};
                bqp_p32(d,p.first); bqp_p32(d+4,s.type); bqp_p64(d+8,s.count); bqp_p64(d+16,offset);
                bqp_p64(d+24,s.bytes); bqp_p64(d+32,s.blocks); bqp_p64(d+40,1);
                bqp_p32(d+60,(uint32_t)crc32(0,d,60)); put(f,d,64); offset+=s.bytes;
            }
            Bytes buffer(1024*1024);
            for(auto &p:sections) {
                require(!fflush(p.second->spool)&&!fseeko(p.second->spool,0,SEEK_SET),"spool seek failed");
                size_t n;
                while((n=fread(buffer.data(),1,buffer.size(),p.second->spool))) put(f,buffer.data(),n);
                require(!ferror(p.second->spool),"spool read failed");
            }
            int rc=fclose(f); f=nullptr; require(!rc,"output close failed");
            /* Link publishes atomically without overwriting a concurrent writer. */
            require(link(name.data(),output.c_str())==0,"cannot publish output (already exists or filesystem error)");
            unlink(name.data());
        } catch(...) { if(f) fclose(f); unlink(name.data()); throw; }
    }
};
