#include "internal.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <sys/resource.h>
#include <unistd.h>
#include <zlib.h>
using Bytes=std::vector<unsigned char>;
using Clock=std::chrono::steady_clock;
static void require(bool ok,const char *s){if(!ok)throw std::runtime_error(s);}
struct Record { uint64_t offset,bytes; };
int main(int argc,char **argv){
    if(argc!=3){std::cerr<<"usage: bqp2optb INPUT.bqp OUTPUT.optb\n";return 2;}
    std::string temporary;FILE *out=nullptr;
    try{
        auto begin=Clock::now();char error[512]={0};bqp_info b{};
        require(!bqp_read_info(argv[1],&b,error,sizeof error),error);
        auto close_file=[](FILE *f){if(f)fclose(f);};
        std::unique_ptr<FILE,decltype(close_file)> in(fopen(argv[1],"rb"),close_file);require(bool(in),"cannot open source");
        require(!std::filesystem::exists(argv[2]),"output already exists");
        std::array<std::vector<Record>,12> records;std::array<uint64_t,12> seen{},bytes{};
        uint64_t position=128;require(!fseeko(in.get(),128,SEEK_SET),"source seek failed");
        for(uint64_t k=0;k<b.blocks;k++){
            unsigned char h[64];require(fread(h,1,64,in.get())==64,"truncated BQP block");
            require(!memcmp(h,"BLK1",4)&&(uint32_t)crc32(0,h,60)==bqp_u32(h+60),"invalid BQP block header");
            unsigned id=bqp_u32(h+4);uint64_t start=bqp_u64(h+8),n=bqp_u64(h+16),sn=bqp_u64(h+40);
            require(id>=1&&id<=11&&start==seen[id]&&n&&n<=b.block_elements&&
                start<=bqp_array_count(&b,id)&&n<=bqp_array_count(&b,id)-start&&sn&&sn<=BQP_MAX_PACKED,
                "invalid BQP block coverage/length");
            uint64_t size=64+sn+(-sn%8);
            require(position<=b.file_bytes&&size<=b.file_bytes-position,"BQP block exceeds file");
            records[id].push_back({position,size});bytes[id]+=size;seen[id]+=n;position+=size;
            require(!fseeko(in.get(),(off_t)position,SEEK_SET),"source seek failed");
        }
        require(position==b.file_bytes,"BQP file coverage mismatch");
        for(unsigned id=1;id<=11;id++)require(seen[id]==bqp_array_count(&b,id),"BQP array coverage mismatch");
        unsigned char h[128]={0};memcpy(h,"OPTBIN2\n",8);bqp_p32(h+8,2);bqp_p32(h+12,128);
        bqp_p64(h+16,11);bqp_p64(h+24,b.blocks);bqp_p64(h+32,b.file_bytes+11*64);
        bqp_p64(h+40,128);bqp_p64(h+48,11*64);bqp_p32(h+56,OPTB_QUADRATIC);bqp_p32(h+60,(uint32_t)b.sense);
        bqp_p64(h+64,b.cols);bqp_p64(h+72,b.rows);memcpy(h+80,&b.objective_offset,8);
        bqp_p64(h+88,b.block_elements);bqp_p32(h+124,(uint32_t)crc32(0,h,124));
        std::string pattern=std::string(argv[2])+".partial.XXXXXX";std::vector<char> name(pattern.begin(),pattern.end());name.push_back(0);
        int fd=mkstemp(name.data());require(fd>=0,"cannot create temporary output");temporary=name.data();
        out=fdopen(fd,"wb");if(!out){close(fd);throw std::runtime_error("cannot open temporary output");}
        auto put=[&](const void *p,size_t n){require(fwrite(p,1,n,out)==n,"output write failed");};
        put(h,128);uint64_t offset=128+11*64;
        for(unsigned id=1;id<=11;id++){
            unsigned char d[64]={0};unsigned type=id==1||id==9?OPTB_U64:id==2||id==10?
                (bqp_element_bytes(&b,id)==4?OPTB_U32:OPTB_U64):OPTB_F64;
            bqp_p32(d,id);bqp_p32(d+4,type);bqp_p64(d+8,seen[id]);bqp_p64(d+16,offset);
            bqp_p64(d+24,bytes[id]);bqp_p64(d+32,records[id].size());bqp_p64(d+40,1);
            bqp_p32(d+60,(uint32_t)crc32(0,d,60));put(d,64);offset+=bytes[id];
        }
        Bytes buffer(1024*1024);
        for(unsigned id=1;id<=11;id++)for(const auto &r:records[id]){
            require(!fseeko(in.get(),(off_t)r.offset,SEEK_SET),"source seek failed");
            for(uint64_t remain=r.bytes;remain;){
                size_t n=(size_t)std::min<uint64_t>(remain,buffer.size());
                require(fread(buffer.data(),1,n,in.get())==n,"source read failed");put(buffer.data(),n);remain-=n;
            }
        }
        int close_status=fclose(out);out=nullptr;require(!close_status,"output close failed");
        require(!optb_visit(temporary.c_str(),nullptr,nullptr,nullptr,error,sizeof error),error);
        require(!link(temporary.c_str(),argv[2]),"output exists or cannot publish");unlink(temporary.c_str());temporary.clear();
        struct rusage usage{};getrusage(RUSAGE_SELF,&usage);
        std::cout<<"{\"bytes\":"<<offset<<",\"seconds\":"<<std::chrono::duration<double>(Clock::now()-begin).count()
            <<",\"max_rss_bytes\":"<<(uint64_t)usage.ru_maxrss*1024
            <<",\"payloads_unchanged\":true,\"validation\":\"framing-and-decoded-crc; full-model-at-load\"}\n";
        return 0;
    }catch(const std::exception &e){if(out)fclose(out);if(!temporary.empty())unlink(temporary.c_str());std::cerr<<e.what()<<'\n';return 1;}
}
