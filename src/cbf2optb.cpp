#define _FILE_OFFSET_BITS 64
#include "writer.hpp"
#include "cbf_parser.hpp"

int main(int argc,char **argv) {
    if(argc<3) { std::cerr<<"usage: cbf2optb INPUT.cbf[.gz] OUTPUT.optb [--block 65536] [--zstd 1] [--encoding all|raw|values|indices]\n"; return 2; }
    try {
        unsigned block=65536,encoding=3; int level=1;
        for(int i=3;i<argc;i+=2) {
            require(i+1<argc,"missing option value"); std::string arg=argv[i];
            if(arg=="--block") { int n=std::stoi(argv[i+1]); require(n>=1&&(unsigned)n<=BQP_MAX_ELEMENTS,"invalid block size"); block=(unsigned)n; }
            else if(arg=="--zstd") { level=std::stoi(argv[i+1]); require(level>=-5&&level<=19,"invalid zstd level"); }
            else if(arg=="--encoding") {
                std::string mode=argv[i+1];
                require(mode=="all"||mode=="raw"||mode=="values"||mode=="indices","invalid encoding mode");
                encoding=mode=="all"?3:mode=="values"?1:mode=="indices"?2:0;
            }
            else throw std::runtime_error("unknown option");
        }
        auto start=Clock::now(); Input input(argv[1]); OptWriter writer(argv[2],OPTB_CONIC,block,level,encoding);
        try { CbfParser<OptWriter> parser(input,writer); parser.run(); }
        catch(const std::exception &e) { throw std::runtime_error("line "+std::to_string(input.line)+": "+e.what()); }
        writer.finish();
        std::cout<<"{\"rows\":"<<writer.info.rows<<",\"cols\":"<<writer.info.cols<<",\"nnz\":"<<writer.count(OPTB_AX)
                 <<",\"file_bytes\":"<<writer.info.file_bytes<<",\"seconds\":"<<std::chrono::duration<double>(Clock::now()-start).count()<<"}\n";
    } catch(const std::exception &e) { std::cerr<<"cbf2optb: "<<e.what()<<"\n"; return 1; }
    return 0;
}
