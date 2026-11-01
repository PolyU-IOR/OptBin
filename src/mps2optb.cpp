#define _FILE_OFFSET_BITS 64
#include "writer.hpp"
#include "mps_parser.hpp"

int main(int argc,char **argv) {
    if(argc<3) { std::cerr<<"usage: mps2optb INPUT.mps[.gz] OUTPUT.optb [--mps-dialect generic|gurobi|mosek] [--block 65536] [--zstd 1] [--encoding all|raw|values|indices]\n"; return 2; }
    unsigned block=65536,encoding=3; int level=1; std::string dialect="generic";
    try {
        for(int i=3;i<argc;i+=2) {
            require(i+1<argc,"missing option value"); std::string arg=argv[i];
            if(arg=="--block") { int b=std::stoi(argv[i+1]); require(b>=1&&(unsigned)b<=BQP_MAX_ELEMENTS,"invalid block size"); block=(unsigned)b; }
            else if(arg=="--zstd") { level=std::stoi(argv[i+1]); require(level>=-5&&level<=19,"invalid zstd level"); }
            else if(arg=="--mps-dialect") dialect=argv[i+1];
            else if(arg=="--encoding") {
                std::string mode=argv[i+1];
                require(mode=="all"||mode=="raw"||mode=="values"||mode=="indices","invalid encoding mode");
                encoding=mode=="all"?3:mode=="values"?1:mode=="indices"?2:0;
            }
            else throw std::runtime_error("unknown option: "+arg);
        }
        require(dialect=="generic"||dialect=="gurobi"||dialect=="mosek","unsupported MPS dialect");
        auto start=Clock::now(); Input input(argv[1]); OptWriter writer(argv[2],OPTB_QUADRATIC,block,level,encoding);
        try { MpsParser<OptWriter> parser(input,writer,dialect); parser.run(); }
        catch(const std::exception &e) { throw std::runtime_error("line "+std::to_string(input.line)+": "+e.what()); }
        writer.source("MPS;dialect="+dialect+";converter="+OPTBIN_VERSION); writer.finish();
        std::cout<<"{\"rows\":"<<writer.info.rows<<",\"cols\":"<<writer.info.cols
                 <<",\"nnz\":"<<writer.count(OPTB_AX)<<",\"qc\":"<<writer.count(OPTB_QC_ROW)
                 <<",\"file_bytes\":"<<writer.info.file_bytes<<",\"seconds\":"
                 <<std::chrono::duration<double>(Clock::now()-start).count()<<"}\n";
    } catch(const std::exception &e) { std::cerr<<"mps2optb: "<<e.what()<<"\n"; return 1; }
    return 0;
}
