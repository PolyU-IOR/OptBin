/* Benchmark-only in-memory sink for the converters' unchanged text parsers.
 * No OptBin encoding, output file, or solver construction is timed here. */
#include "mps_parser.hpp"
#include "cbf_parser.hpp"

class ArraySink {
    struct Array { unsigned type; Bytes bytes; };
    std::map<unsigned,Array> arrays;
    std::vector<optb_section> sections;
public:
    optb_info info{};
    explicit ArraySink(unsigned profile) { info.profile=profile; info.sense=1; }
    void declare(unsigned id,unsigned type) {
        auto it=arrays.emplace(id,Array{type,{}});
        require(it.second||it.first->second.type==type,"section type changed");
    }
    void append(unsigned id,unsigned type,const void *data,size_t count) {
        declare(id,type); auto &a=arrays.at(id); unsigned width=optb_type_bytes(type);
        require(width&&count<=(SIZE_MAX-a.bytes.size())/width,"section overflow");
        if(count) {
            auto p=static_cast<const unsigned char*>(data);
            a.bytes.insert(a.bytes.end(),p,p+count*width);
        }
    }
    void integer(unsigned id,uint64_t v) {
        unsigned type=arrays.count(id)?arrays.at(id).type:unsigned(OPTB_U64);
        require(type==OPTB_U32||type==OPTB_U64,"integer type required");
        if(type==OPTB_U32) { require(v<=UINT32_MAX,"index overflow"); uint32_t x=uint32_t(v); append(id,type,&x,1); }
        else append(id,type,&v,1);
    }
    void real(unsigned id,double v) { append(id,OPTB_F64,&v,1); }
    template<class T> void vector(unsigned id,unsigned type,const std::vector<T> &v) {
        require(sizeof(T)==optb_type_bytes(type),"vector type mismatch");
        append(id,type,v.data(),v.size());
    }
    void source(const std::string &) {} // Source strings do not affect the numerical model.
    optb_model view() {
        sections.clear(); sections.reserve(arrays.size());
        for(auto &item:arrays) {
            optb_section s{}; s.id=item.first; s.type=item.second.type;
            s.count=item.second.bytes.size()/optb_type_bytes(s.type);
            s.data=item.second.bytes.data(); sections.push_back(s);
        }
        info.sections=sections.size(); return {info,sections.data()};
    }
};

/* Kept alive until process exit, just like the binary model at the RSS snapshot.
 * Only the single measured model uses this path; reference loads use OptBin. */
static ArraySink *retained=nullptr;
extern "C" int optb_text_bench_load(const char *kind,const char *path,optb_model *out,char *error,size_t n) {
    try {
        require(!retained,"only one text model per process");
        bool cbf=!strcmp(kind,"cbf");
        require(cbf||!strcmp(kind,"mps-generic")||!strcmp(kind,"mps-gurobi")||!strcmp(kind,"mps-mosek"),"unknown text reader");
        retained=new ArraySink(cbf?OPTB_CONIC:OPTB_QUADRATIC);
        {
            Input input(path);
            if(cbf) { CbfParser<ArraySink> parser(input,*retained); parser.run(); }
            else { MpsParser<ArraySink> parser(input,*retained,std::string(kind+4)); parser.run(); }
        }
        *out=retained->view(); require(!optb_validate(out,error,n),error);
        return 0;
    } catch(const std::exception &e) {
        if(error&&n) snprintf(error,n,"%s",e.what());
        *out={}; delete retained; retained=nullptr; return -1;
    }
}

extern "C" void optb_text_bench_free(optb_model *model) {
    delete retained; retained=nullptr; *model={};
}
