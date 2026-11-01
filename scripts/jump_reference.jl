# Independent source reader: no OptBin parser, library, or writer is loaded here.
using JuMP
import MathOptInterface as MOI
import JSON3
import Pkg
import SHA

bits(x) = string(reinterpret(UInt64, Float64(x)); base = 16, pad = 16)

function scalar_function(f, indices)
    linear = Any[]
    quadratic = Any[]
    constant = 0.0
    if f isa MOI.VariableIndex
        push!(linear, [indices[f], bits(1.0)])
    elseif f isa MOI.ScalarAffineFunction{Float64}
        append!(linear, [[indices[t.variable], bits(t.coefficient)] for t in f.terms])
        constant = f.constant
    elseif f isa MOI.ScalarQuadraticFunction{Float64}
        append!(linear, [[indices[t.variable], bits(t.coefficient)] for t in f.affine_terms])
        # MOI uses half the coefficient for a repeated-variable quadratic term.
        append!(quadratic, [[indices[t.variable_1], indices[t.variable_2],
            bits(t.coefficient * (t.variable_1 == t.variable_2 ? 0.5 : 1.0))]
            for t in f.quadratic_terms])
        constant = f.constant
    else
        error("Unsupported scalar function in comparison: $(typeof(f))")
    end
    return Dict("kind" => "scalar", "linear" => linear,
                "quadratic" => quadratic, "constant" => bits(constant))
end

function vector_function(f, indices)
    terms = Any[]
    if f isa MOI.VectorOfVariables
        append!(terms, [[i - 1, indices[v], bits(1.0)] for (i, v) in enumerate(f.variables)])
        constants = zeros(length(f.variables))
    elseif f isa MOI.VectorAffineFunction{Float64}
        append!(terms, [[t.output_index - 1, indices[t.scalar_term.variable], bits(t.scalar_term.coefficient)]
                       for t in f.terms])
        constants = f.constants
    else
        error("Unsupported vector function in comparison: $(typeof(f))")
    end
    return Dict("kind" => "vector", "terms" => terms, "constants" => bits.(constants))
end

function serialize_set(s)
    if s isa MOI.LessThan{Float64}
        return Dict("kind" => "interval", "lower" => bits(-Inf), "upper" => bits(s.upper))
    elseif s isa MOI.GreaterThan{Float64}
        return Dict("kind" => "interval", "lower" => bits(s.lower), "upper" => bits(Inf))
    elseif s isa MOI.EqualTo{Float64}
        return Dict("kind" => "interval", "lower" => bits(s.value), "upper" => bits(s.value))
    elseif s isa MOI.Interval{Float64}
        return Dict("kind" => "interval", "lower" => bits(s.lower), "upper" => bits(s.upper))
    elseif s isa MOI.AbstractVectorSet
        name = string(nameof(typeof(s)))
        result = Dict{String,Any}("kind" => name, "dimension" => MOI.dimension(s))
        if s isa Union{MOI.PowerCone,MOI.DualPowerCone}
            result["exponent"] = bits(s.exponent)
        elseif s isa MOI.PositiveSemidefiniteConeTriangle
            result["side_dimension"] = s.side_dimension
        end
        return result
    else
        error("Unsupported set in continuous comparison: $(typeof(s))")
    end
end

function export_model(path, format, dialect)
    fmt = format == "mps" ? MOI.FileFormats.FORMAT_MPS : MOI.FileFormats.FORMAT_CBF
    options = format == "mps" ?
        (; quadratic_format = dialect == "mosek" ? MOI.FileFormats.MPS.kQuadraticFormatMosek : MOI.FileFormats.MPS.kQuadraticFormatGurobi) :
        (;)
    model = JuMP.read_from_file(path; format = fmt, options...)
    backend = JuMP.backend(model)
    variables = MOI.get(backend, MOI.ListOfVariableIndices())
    indices = Dict(v => i - 1 for (i, v) in enumerate(variables))
    objective_type = MOI.get(backend, MOI.ObjectiveFunctionType())
    objective = scalar_function(MOI.get(backend, MOI.ObjectiveFunction{objective_type}()), indices)
    constraints = Any[]
    for (F, S) in MOI.get(backend, MOI.ListOfConstraintTypesPresent())
        for ci in MOI.get(backend, MOI.ListOfConstraintIndices{F,S}())
            f = MOI.get(backend, MOI.ConstraintFunction(), ci)
            s = MOI.get(backend, MOI.ConstraintSet(), ci)
            encoded = f isa MOI.AbstractScalarFunction ? scalar_function(f, indices) : vector_function(f, indices)
            push!(constraints, Dict("function" => encoded, "set" => serialize_set(s)))
        end
    end
    return Dict("variables" => length(variables), "objective" => objective, "constraints" => constraints,
        "sense" => MOI.get(backend, MOI.ObjectiveSense()) == MOI.MAX_SENSE ? -1 : 1,
        "source_sha256" => bytes2hex(SHA.sha256(read(path))))
end

function main()
    length(ARGS) == 2 || error("usage: jump_reference.jl CASES.json OUTPUT.jsonl")
    dependencies = Pkg.dependencies()
    versions = Dict(info.name => Dict("version" => string(info.version), "git_tree_sha1" => info.tree_hash)
                    for info in values(dependencies) if info.name in ["JuMP", "MathOptInterface", "JSON3"])
    reference_files = Dict{String,String}()
    moi_root = pkgdir(MOI)
    for file in ["src/FileFormats/MPS/MPS.jl", "src/FileFormats/MPS/read.jl", "src/FileFormats/CBF/read.jl"]
        path = joinpath(moi_root, file)
        isfile(path) && (reference_files[file] = bytes2hex(SHA.sha256(read(path))))
    end
    open(ARGS[2], "w") do output
        println(output, JSON3.write(Dict("environment" => Dict("julia" => string(VERSION),
            "packages" => versions, "reference_source_sha256" => reference_files,
            "exporter_sha256" => bytes2hex(SHA.sha256(read(@__FILE__)))))))
        flush(output)
        for item in JSON3.read(read(ARGS[1], String))
            start = time()
            result = Dict{String,Any}("id" => String(item.id),
                "source_sha256" => bytes2hex(SHA.sha256(read(String(item.path)))))
            try
                dialect = haskey(item, :reference_dialect) ? String(item.reference_dialect) :
                    (haskey(item, :dialect) && !isnothing(item.dialect) ? String(item.dialect) : "none")
                result["reference_dialect"] = dialect
                merge!(result, export_model(String(item.path), String(item.format), dialect))
                result["status"] = "read"
            catch error
                result["status"] = "reference_error"
                result["error_type"] = string(typeof(error))
                result["error"] = sprint(showerror, error)
            end
            result["seconds"] = time() - start
            println(output, JSON3.write(result)); flush(output)
            println(item.id, " ", result["status"])
            flush(stdout)
        end
    end
end

main()
