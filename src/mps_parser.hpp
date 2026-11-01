#pragma once
#include "converter_common.hpp"

template<class Writer>
class MpsParser {
    Input &in; Writer &w; Names rows,cols;
    std::vector<char> rowtype,lower_set;
    std::vector<double> rhs,ranges,c,lo,hi;
    std::vector<std::pair<uint64_t,double>> entries;
    using Quad=std::map<std::pair<uint64_t,uint64_t>,double>;
    Quad quadratic; Quad *current_quad=&quadratic;
    std::map<uint64_t,Quad> constraints;
    std::map<uint64_t,std::string> constraint_kind;
    std::string dialect;
    static std::map<std::pair<uint64_t,uint64_t>,double> canonical(const Quad &q,const std::string &kind) {
        std::map<std::pair<uint64_t,uint64_t>,double> upper;
        int triangle=0;
        for(const auto &item:q) {
            uint64_t a=item.first.first,b=item.first.second;
            if((kind=="QMATRIX"||kind=="QCMATRIX")&&a!=b) {
                auto other=q.find({b,a});
                require(other!=q.end()&&bits(other->second)==bits(item.second),"quadratic matrix must provide matching symmetric entries");
                if(a>b) continue;
            } else if(a!=b) {
                int side=a<b?1:-1; require(!triangle||side==triangle,"triangular quadratic section mixes both triangles"); triangle=side;
            }
            require(upper.emplace(std::make_pair(std::max(a,b),std::min(a,b)),item.second).second,"duplicate quadratic entry");
        }
        return upper;
    }
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
            w.info.rows=rows.size(); w.declare(OPTB_AP,OPTB_U64); w.declare(OPTB_AI,rows.size()<=UINT32_MAX?OPTB_U32:OPTB_U64); w.declare(OPTB_AX,OPTB_F64);
            w.integer(BQP_AP,0);
        }
        bool quad=next=="QUADOBJ"||next=="QMATRIX"||next=="QSECTION"||next=="QCMATRIX";
        if(quad) {
            require(seen_columns,"quadratic section before COLUMNS");
            if(next=="QCMATRIX"||next=="QSECTION") {
                require(n==2,"quadratic section requires row name");
                if(next=="QCMATRIX") require(dialect=="gurobi"||dialect=="mosek","QCMATRIX requires explicit --mps-dialect gurobi or mosek");
                if(next=="QSECTION") require(dialect=="mosek","QSECTION requires --mps-dialect mosek");
                if(next=="QSECTION"&&obj==p[1]) {
                    require(qsection.empty(),"multiple quadratic objective sections"); qsection=next; current_quad=&quadratic;
                } else {
                    uint64_t row=rows.find(p[1]);
                    require(!constraint_kind.count(row),"repeated quadratic constraint section");
                    constraint_kind[row]=next; current_quad=&constraints[row];
                }
            } else {
                require(n==1&&qsection.empty(),"multiple/invalid quadratic objective sections");
                qsection=next; current_quad=&quadratic;
            }
        }
        if(next=="ENDATA") ended=true;
        section=next;
        if((next=="OBJSENSE"||next=="OBJNAME")&&n==2) metadata(p[1]);
        else require(quad||n==1||next=="NAME", "unexpected fields on section header");
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
    MpsParser(Input &i,Writer &o,const std::string &d):in(i),w(o),dialect(d) {}
    uint64_t run() {
        char *line;
        while((line=in.next())) {
            if(*line=='*') continue;
            bool header_line=*line!=' '&&*line!='\t'&&*line!='\r'&&*line!=0;
            std::array<char*,12> p{}; unsigned n=tokenize(line,p); if(!n) continue;
            require(!ended,"unexpected data after ENDATA");
            bool keyword=!strcmp(p[0],"NAME")||!strcmp(p[0],"OBJSENSE")||!strcmp(p[0],"OBJNAME")||
                !strcmp(p[0],"ROWS")||!strcmp(p[0],"COLUMNS")||!strcmp(p[0],"RHS")||!strcmp(p[0],"RANGES")||
                !strcmp(p[0],"BOUNDS")||!strcmp(p[0],"QUADOBJ")||!strcmp(p[0],"QMATRIX")||
                !strcmp(p[0],"QCMATRIX")||!strcmp(p[0],"QSECTION")||!strcmp(p[0],"ENDATA");
            bool named_header=!strcmp(p[0],"NAME")||!strcmp(p[0],"OBJSENSE")||!strcmp(p[0],"OBJNAME")||
                !strcmp(p[0],"QCMATRIX")||!strcmp(p[0],"QSECTION");
            if(header_line&&keyword&&(n==1||(named_header&&n==2))) {
                std::string s=p[0];
                require(s=="NAME"||s=="OBJSENSE"||s=="OBJNAME"||s=="ROWS"||s=="COLUMNS"||s=="RHS"||s=="RANGES"||s=="BOUNDS"||s=="QUADOBJ"||s=="QMATRIX"||s=="QCMATRIX"||s=="QSECTION"||s=="ENDATA",
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
            } else if(section=="QUADOBJ"||section=="QMATRIX"||section=="QCMATRIX"||section=="QSECTION") {
                require(n==3,"bad quadratic record"); uint64_t a=cols.find(p[0]),b=cols.find(p[1]);
                auto key=std::make_pair(a,b); double v=number(p[2]);
                auto it=current_quad->find(key);
                if(it==current_quad->end()) current_quad->emplace(key,v);
                else { it->second+=v; require(std::isfinite(it->second),"quadratic duplicate overflow"); }
            } else throw std::runtime_error("data in unsupported section: "+section);
            if((in.line&65535)==0&&std::chrono::duration<double>(Clock::now()-report).count()>10) {
                std::cerr<<"progress line="<<in.line<<" compressed_read="<<in.compressed_position()<<" cols="<<cols.size()<<" nnz="<<nnz<<"\n"; report=Clock::now();
            }
        }
        require(ended&&seen_columns,"missing ENDATA/COLUMNS (truncated input)");
        require(!obj.empty()&&(obj_requested.empty()||obj_requested==obj),"objective row not found");
        w.info.cols=cols.size(); w.declare(OPTB_QP,OPTB_U64); w.declare(OPTB_QI,cols.size()<=UINT32_MAX?OPTB_U32:OPTB_U64); w.declare(OPTB_QX,OPTB_F64);
        w.declare(OPTB_RL,OPTB_F64); w.declare(OPTB_RU,OPTB_F64);
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
        w.vector(OPTB_C,OPTB_F64,c); w.vector(OPTB_XL,OPTB_F64,lo); w.vector(OPTB_XU,OPTB_F64,hi);
        auto upper=canonical(quadratic,qsection);
        if(!upper.empty()) {
            uint64_t j=0,k=0; w.integer(BQP_QP,0);
            for(const auto &item:upper) {
                while(j<item.first.first) { w.integer(BQP_QP,k); ++j; }
                w.integer(BQP_QI,item.first.second); w.real(BQP_QX,item.second); ++k;
            }
            while(j<cols.size()) { w.integer(BQP_QP,k); ++j; }
        }
        if(!constraints.empty()) {
            for(unsigned id=OPTB_QC_ROW;id<=OPTB_QC_SCALE;id++)
                w.declare(id,id==OPTB_QC_X||id==OPTB_QC_SCALE?OPTB_F64:OPTB_U64);
            uint64_t total=0; w.integer(OPTB_QC_PTR,0);
            for(const auto &entry:constraints) {
                w.integer(OPTB_QC_ROW,entry.first);
                w.real(OPTB_QC_SCALE,constraint_kind.at(entry.first)=="QCMATRIX"?1.:.5);
                for(const auto &term:canonical(entry.second,constraint_kind.at(entry.first))) {
                    w.integer(OPTB_QC_I,term.first.second); w.integer(OPTB_QC_J,term.first.first);
                    w.real(OPTB_QC_X,term.second); ++total;
                }
                w.integer(OPTB_QC_PTR,total);
            }
        }
        return input_nnz;
    }
};
