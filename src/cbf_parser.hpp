#pragma once
#include "converter_common.hpp"

template<class Writer>
class CbfParser {
    Input &in; Writer &w;
    unsigned group=0;
    std::set<std::string> seen;
    std::map<std::string,std::vector<std::vector<double>>> power;
    struct Cone { unsigned kind; uint64_t dim; std::vector<double> param; };
    std::vector<Cone> vars,cons;
    std::vector<uint64_t> psdvar,psdcon;
    std::map<uint64_t,double> obj,b;
    struct AEntry { uint64_t row,col; double value; };
    struct MatrixEntry { std::array<uint64_t,4> key; double value; };
    std::vector<AEntry> a;
    std::vector<MatrixEntry> of,f,h,d;
    static uint64_t integer(const std::string &s) {
        uint64_t n=0; auto r=std::from_chars(s.data(),s.data()+s.size(),n);
        require(r.ec==std::errc()&&r.ptr==s.data()+s.size()&&n<INT64_MAX,"invalid CBF integer"); return n;
    }
    std::vector<std::string> fields(bool keyword=false) {
        for(;;) {
            char *line=in.next(); require(line,"truncated CBF input");
            if(*line=='#') { require(keyword,"CBF comments are only allowed between blocks"); continue; }
            std::array<char*,12> p{}; unsigned n=tokenize(line,p);
            if(!n) { require(keyword,"empty line inside CBF block"); continue; }
            std::vector<std::string> result; for(unsigned j=0;j<n;j++) result.emplace_back(p[j]); return result;
        }
    }
    std::vector<std::string> row(size_t n) {
        auto p=fields(); require(p.size()==n,"wrong CBF record field count"); return p;
    }
    uint64_t scalar_count() { return integer(row(1)[0]); }
    std::vector<uint64_t> dimensions() {
        uint64_t n=scalar_count(); std::vector<uint64_t> result;
        for(uint64_t j=0;j<n;j++) { uint64_t size=scalar_count(); require(size&&size<=UINT32_MAX,"invalid PSD order"); result.push_back(size); }
        return result;
    }
    Cone cone(const std::string &name,uint64_t dim) {
        static const std::map<std::string,unsigned> known={{"F",OPTB_FREE},{"L+",OPTB_NONNEG},
            {"L-",OPTB_NONPOS},{"L=",OPTB_ZERO},{"Q",OPTB_SOC},{"QR",OPTB_RSOC},
            {"EXP",OPTB_EXP},{"EXP*",OPTB_DUAL_EXP}};
        auto it=known.find(name);
        if(it!=known.end()) {
            require(dim&&(!(it->second==OPTB_RSOC)||dim>=2)&&
                    (!(it->second==OPTB_EXP||it->second==OPTB_DUAL_EXP)||dim==3),"invalid cone dimension");
            return {it->second,dim,{}};
        }
        auto colon=name.find(':');
        require(!name.empty()&&name[0]=='@'&&colon!=std::string::npos,"unsupported cone: "+name);
        uint64_t index=integer(name.substr(1,colon-1)); std::string table=name.substr(colon+1);
        require((table=="POW"||table=="POW*")&&power.count(table)&&index<power.at(table).size(),"unknown parameterized cone");
        auto param=power.at(table)[index]; require(dim>=param.size()&&!param.empty(),"power cone parameter dimension");
        return {table=="POW"?OPTB_POWER:OPTB_DUAL_POWER,dim,std::move(param)};
    }
    std::vector<Cone> domains(uint64_t &dimension) {
        auto header=row(2); dimension=integer(header[0]); uint64_t k=integer(header[1]),total=0;
        std::vector<Cone> result;
        for(uint64_t j=0;j<k;j++) {
            auto p=row(2); uint64_t dim=integer(p[1]); require(total<=dimension&&dim<=dimension-total,"cone dimension overflow");
            result.push_back(cone(p[0],dim)); total+=dim;
        }
        require(total==dimension,"cone dimensions do not sum to domain"); return result;
    }
    void write_cones(unsigned base,const std::vector<Cone> &v) {
        w.declare(base,OPTB_U32); w.declare(base+1,OPTB_U64); w.declare(base+2,OPTB_U64); w.declare(base+3,OPTB_F64);
        uint64_t total=0; w.integer(base+2,0);
        for(const auto &c:v) {
            w.integer(base,c.kind); w.integer(base+1,c.dim);
            w.vector(base+3,OPTB_F64,c.param); total+=c.param.size(); w.integer(base+2,total);
        }
    }
    void matrix_coords(std::vector<MatrixEntry> &v,unsigned prefix) {
        uint64_t n=scalar_count();
        for(uint64_t k=0;k<n;k++) {
            auto p=row(prefix+3); MatrixEntry t{{0,0,0,0},number(p.back().c_str())};
            for(unsigned j=0;j<prefix;j++) t.key[j]=integer(p[j]);
            uint64_t i=integer(p[prefix]),col=integer(p[prefix+1]);
            t.key[2]=std::min(i,col); t.key[3]=std::max(i,col); v.push_back(t);
        }
    }
    void write_matrix(std::vector<MatrixEntry> &v,unsigned first,unsigned prefix,
                      const std::vector<uint64_t> &dims,unsigned block_key) {
        for(unsigned id=first;id<first+prefix+2;id++) w.declare(id,OPTB_U64);
        w.declare(first+prefix+2,OPTB_F64);
        auto key=[](const MatrixEntry &t) { return std::make_tuple(t.key[0],t.key[1],t.key[3],t.key[2]); };
        std::sort(v.begin(),v.end(),[&](const auto &a,const auto &b){return key(a)<key(b);});
        for(size_t k=0;k<v.size();k++) {
            const auto &t=v[k]; uint64_t block=t.key[block_key];
            require(block<dims.size()&&t.key[3]<dims[block],"PSD coordinate out of bounds");
            require(!k||key(v[k-1])!=key(t),"duplicate/mirrored PSD coordinate");
            if(first==OPTB_F_ROW) require(t.key[0]<w.info.rows,"F row out of bounds");
            if(first==OPTB_H_BLOCK) require(t.key[1]<w.info.cols,"H variable out of bounds");
            for(unsigned j=0;j<prefix;j++) w.integer(first+j,t.key[j]);
            w.integer(first+prefix,t.key[2]); w.integer(first+prefix+1,t.key[3]); w.real(first+prefix+2,t.value);
        }
    }
public:
    CbfParser(Input &input,Writer &writer):in(input),w(writer) {}
    void run() {
        // Check VER before any model declarations. Do not guess later versions.
        require(fields(true)==std::vector<std::string>{"VER"},"CBF must begin with VER");
        uint64_t version=scalar_count(); require(version>=1&&version<=3,"supported CBF versions are 1, 2, 3");
        seen.insert("VER"); group=1;
        for(;;) {
            char *line=in.next(); if(!line) break;
            if(*line=='#') continue;
            std::array<char*,12> p{}; unsigned n=tokenize(line,p); if(!n) continue;
            require(n==1,"CBF keyword must be alone"); std::string kw=p[0];
            require(kw!="INT","mixed-integer CBF unsupported; integrality cannot be dropped");
            require(kw!="CHANGE","CBF change sequences unsupported; select a snapshot explicitly before conversion");
            require(seen.insert(kw).second,"duplicate CBF keyword: "+kw);
            if(kw=="POWCONES"||kw=="POW*CONES") {
                require(version>=3&&group<=1,"invalid parametric cone table order/version");
                auto p=row(2); uint64_t k=integer(p[0]),total=integer(p[1]),used=0;
                std::string name=kw=="POWCONES"?"POW":"POW*";
                for(uint64_t j=0;j<k;j++) {
                    uint64_t dim=scalar_count(); require(dim&&used<=total&&dim<=total-used,"power table count mismatch");
                    std::vector<double> alpha;
                    for(uint64_t t=0;t<dim;t++) { double x=number(row(1)[0].c_str()); require(x>0,"power weights must be positive"); alpha.push_back(x); }
                    power[name].push_back(std::move(alpha)); used+=dim;
                }
                require(used==total,"power table total mismatch");
            } else if(kw=="OBJSENSE"||kw=="VAR"||kw=="CON"||kw=="PSDVAR"||kw=="PSDCON") {
                require(group<=2,"structure after numerical data"); group=2;
                if(kw=="OBJSENSE") { auto sense=row(1)[0]; require(sense=="MIN"||sense=="MAX","invalid objective sense"); w.info.sense=sense=="MIN"?1:-1; }
                else if(kw=="VAR") { require(!seen.count("CON")&&!seen.count("PSDCON"),"VAR after constraints"); vars=domains(w.info.cols); }
                else if(kw=="CON") cons=domains(w.info.rows);
                else if(kw=="PSDVAR") { require(!seen.count("CON")&&!seen.count("PSDCON"),"PSDVAR after constraints"); psdvar=dimensions(); }
                else psdcon=dimensions();
            } else {
                group=3;
                if(kw=="OBJBCOORD") w.info.objective_offset=number(row(1)[0].c_str());
                else if(kw=="OBJACOORD"||kw=="BCOORD") {
                    auto &target=kw=="OBJACOORD"?obj:b; uint64_t dim=kw=="OBJACOORD"?w.info.cols:w.info.rows;
                    uint64_t nnz=scalar_count();
                    for(uint64_t j=0;j<nnz;j++) {
                        auto p=row(2); uint64_t id=integer(p[0]); require(id<dim,"vector coordinate out of bounds");
                        require(target.emplace(id,number(p[1].c_str())).second,"duplicate vector coordinate");
                    }
                } else if(kw=="ACOORD") {
                    uint64_t nnz=scalar_count();
                    for(uint64_t j=0;j<nnz;j++) {
                        auto p=row(3); uint64_t rowid=integer(p[0]),col=integer(p[1]);
                        require(rowid<w.info.rows&&col<w.info.cols,"A coordinate out of bounds");
                        a.push_back({rowid,col,number(p[2].c_str())});
                    }
                } else if(kw=="OBJFCOORD") matrix_coords(of,1);
                else if(kw=="FCOORD") matrix_coords(f,2);
                else if(kw=="HCOORD") matrix_coords(h,2);
                else if(kw=="DCOORD") matrix_coords(d,1);
                else throw std::runtime_error("unsupported CBF keyword: "+kw);
            }
        }
        require(seen.count("OBJSENSE"),"missing OBJSENSE");
        require(seen.count("VAR")||seen.count("PSDVAR"),"missing variable structure");
        w.declare(OPTB_AP,OPTB_U64); w.declare(OPTB_AI,w.info.rows<=UINT32_MAX?OPTB_U32:OPTB_U64); w.declare(OPTB_AX,OPTB_F64);
        std::sort(a.begin(),a.end(),[](const auto &x,const auto &y){return std::tie(x.col,x.row)<std::tie(y.col,y.row);});
        uint64_t col=0,total=0; w.integer(OPTB_AP,0);
        for(size_t k=0;k<a.size();k++) {
            const auto &t=a[k];
            require(!k||a[k-1].col!=t.col||a[k-1].row!=t.row,"duplicate A coordinate");
            while(col<t.col) { w.integer(OPTB_AP,total); ++col; }
            w.integer(OPTB_AI,t.row); w.real(OPTB_AX,t.value); ++total;
        }
        while(col<w.info.cols) { w.integer(OPTB_AP,total); ++col; }
        w.declare(OPTB_C,OPTB_F64); for(uint64_t j=0;j<w.info.cols;j++) w.real(OPTB_C,obj.count(j)?obj.at(j):0.);
        w.declare(OPTB_B,OPTB_F64); for(uint64_t j=0;j<w.info.rows;j++) w.real(OPTB_B,b.count(j)?b.at(j):0.);
        write_cones(OPTB_VAR_KIND,vars); write_cones(OPTB_CON_KIND,cons);
        w.vector(OPTB_PSD_VAR_DIM,OPTB_U64,psdvar); w.vector(OPTB_PSD_CON_DIM,OPTB_U64,psdcon);
        write_matrix(of,OPTB_OF_BLOCK,1,psdvar,0); write_matrix(f,OPTB_F_ROW,2,psdvar,1);
        write_matrix(h,OPTB_H_BLOCK,2,psdcon,0); write_matrix(d,OPTB_D_BLOCK,1,psdcon,0);
        w.source("CBF;version="+std::to_string(version));
    }
};
