package texhelper.chatservice.domain.law.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.common.entity.Timestamped;

import java.util.ArrayList;
import java.util.List;


@Getter
@Entity
@Table(name = "law")
@NoArgsConstructor
@Builder
@AllArgsConstructor
/**
 * https://open.law.go.kr/LSO/openApi/guideResult.do
 *  법령의 기본 정보를 Law 분리하여 테이블로 만들었습니다.
 */
public class Law extends Timestamped {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column
    private Long lawId;

    @Column
    private Long lawKey;

    @Column
    private String nameKor;

    @Column
    private String nameHanja;

    @Column
    private String nameShort;

    @Column
    private String decisionType;

    @Column
    private String proposalType;

    @Column
    private String proclamationNo;

    @Column
    private String proclamationDate;

    @Column
    private String enforcementDate;

    @Column
    private String phoneNumber;

    @Column
    private String language;

    @Column
    private String revisionType;

    @Column
    private String revisionClassification;

    @Column
    private String joinMinistry;

    @Column
    private String isProclaimed;

    @Column
    private String isKorean;

    @Column
    private String isTitleChange;

    @Column
    private String hasAnnex;

    @Column
    private String structureCode;

    @OneToMany(mappedBy = "law", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Articles> articles =  new ArrayList<>();

    @OneToMany(mappedBy = "law", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Supplements> supplements =  new ArrayList<>();

    @OneToMany(mappedBy = "law", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Amendments> amendments =  new ArrayList<>();

    @OneToMany(mappedBy = "law", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Ministry> ministry = new ArrayList<>();

    @OneToMany(mappedBy = "law", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<LawType> lawTypes = new ArrayList<>();

    public Law(Long id, Long lawId, Long lawKey, String nameKor, String nameHanja, String nameShort, String decisionType,
               String proposalType, String proclamationNo, String proclamationDate, String enforcementDate,
               String phoneNumber, String language, String revisionType, String joinMinistry,
               String isProclaimed, String isKorean, String isTitleChange, String hasAnnex, String structureCode,
               List<Articles> articles, List<Supplements> supplements, List<Amendments> amendments,
               List<Ministry> ministry,  List<LawType> lawTypes) {
        this.id = id;
        this.lawId =  lawId;
        this.lawKey = lawKey;
        this.nameKor = nameKor;
        this.nameHanja = nameHanja;
        this.nameShort = nameShort;
        this.decisionType = decisionType;
        this.proposalType = proposalType;
        this.proclamationNo = proclamationNo;
        this.proclamationDate = proclamationDate;
        this.enforcementDate = enforcementDate;
        this.phoneNumber = phoneNumber;
        this.language = language;
        this.revisionType = revisionType;
        this.joinMinistry = joinMinistry;
        this.isProclaimed = isProclaimed;
        this.isKorean = isKorean;
        this.isTitleChange = isTitleChange;
        this.hasAnnex = hasAnnex;
        this.structureCode = structureCode;
        this.articles = articles;
        this.supplements = supplements;
        this.amendments = amendments;
        this.ministry = ministry;
        this.lawTypes = lawTypes;
    }

    public void initMinistryList() {
        if (this.ministry == null) this.ministry = new ArrayList<>();
    }

    public void  addLawType (LawType l){
        if(this.lawTypes == null){
            this.lawTypes = new ArrayList<>();
        }

        this.lawTypes.add(l);
        l.assignLawType(this);
    }
}
